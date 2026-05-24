#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WeChat-only SaveXTube entrypoint.

Run modes:
  python3 savextube_wechat.py login
  python3 savextube_wechat.py login --bot wife
  python3 savextube_wechat.py run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence

import requests

os.environ.setdefault("SAVEXTUBE_WECHAT_ONLY", "1")

from clawbot_wechat import FINDER_FEED_CARD_MARKER, ClawBotClient, ClawBotError, WeChatInboundMessage  # noqa: E402
from config_reader import get_proxy_config, load_toml_config  # noqa: E402

logger = logging.getLogger("savextube.wechat_runner")

DEFAULT_SUPPORTED_PLATFORMS = {"douyin", "kuaishou", "weibo", "toutiao", "xiaohongshu", "bilibili", "wechat_channels"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".avi", ".ts"}
MAX_REMEMBERED_MESSAGES = 1000
FILE_MTIME_TOLERANCE_SECONDS = 1.0
BOT_INHERIT_EXCLUDE_KEYS = {"bots", "enabled", "name", "session_file", "token"}
PENDING_LOGIN_WARNED: set[str] = set()


def _first_existing_config_path() -> str:
    configured = os.getenv("SAVEXTUBE_CONFIG", "").strip()
    candidates = [
        configured,
        "/app/config/savextube.toml",
        "savextube.toml",
        "config.toml",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return configured or "/app/config/savextube.toml"


def _load_config() -> Dict[str, Any]:
    path = _first_existing_config_path()
    config = load_toml_config(path)
    if config:
        logger.info("loaded config: %s", path)
    return config or {}


def _configure_logging(config: Dict[str, Any]) -> None:
    logging_config = config.get("logging") or {}
    level_name = str(logging_config.get("log_level") or os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    handlers: List[logging.Handler] = []

    if _parse_bool(logging_config.get("log_to_console"), default=True):
        handlers.append(logging.StreamHandler())

    if _parse_bool(logging_config.get("log_to_file"), default=True):
        log_dir = Path(str(logging_config.get("log_dir") or os.getenv("LOG_DIR", "/app/logs")))
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            max_size_mb = _parse_int(logging_config.get("log_max_size"), default=10)
            backup_count = _parse_int(logging_config.get("log_backup_count"), default=5, minimum=0)
            handlers.append(
                RotatingFileHandler(
                    log_dir / "savextube-wechat.log",
                    maxBytes=max_size_mb * 1024 * 1024,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            handlers.append(logging.StreamHandler())
            logging.getLogger("savextube.wechat_runner").warning("file logging disabled: %s", exc)

    logging.basicConfig(level=level, format=log_format, handlers=handlers or None, force=True)


def _parse_allowed_users(raw: str) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()}


def _parse_supported_platforms(raw: str) -> set[str]:
    if not raw:
        return set(DEFAULT_SUPPORTED_PLATFORMS)
    values = {part.strip().lower() for part in re.split(r"[,;\s]+", raw) if part.strip()}
    return values or set(DEFAULT_SUPPORTED_PLATFORMS)


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _parse_float(value: Any, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default


def _safe_bot_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "").strip("._-")
    return cleaned[:64] or "default"


def _default_session_file(bot_name: str, multi_bot: bool = False) -> str:
    if not multi_bot and bot_name == "default":
        return os.getenv("WECHAT_SESSION_FILE", "/app/config/wechat_session.json")
    return f"/app/config/wechat_{_safe_bot_name(bot_name)}.json"


def _bot_profiles(config: Dict[str, Any], include_disabled: bool = False) -> List[Dict[str, Any]]:
    wechat = config.get("wechat") or {}
    raw_bots = wechat.get("bots") or []
    if not raw_bots:
        profile = dict(wechat)
        profile["name"] = str(profile.get("name") or os.getenv("WECHAT_BOT_NAME", "default"))
        profile["session_file"] = str(profile.get("session_file") or _default_session_file(profile["name"]))
        return [profile]

    inherited = {key: value for key, value in wechat.items() if key not in BOT_INHERIT_EXCLUDE_KEYS}
    profiles: List[Dict[str, Any]] = []
    for index, raw_profile in enumerate(raw_bots, start=1):
        if not isinstance(raw_profile, dict):
            continue
        profile = {**inherited, **raw_profile}
        profile["name"] = _safe_bot_name(str(profile.get("name") or f"bot{index}"))
        profile["session_file"] = str(
            profile.get("session_file")
            or _default_session_file(profile["name"], multi_bot=True)
        )
        if not include_disabled and not _parse_bool(profile.get("enabled"), default=True):
            continue
        profiles.append(profile)
    return profiles


def _resolve_bot_profile(config: Dict[str, Any], bot_name: str = "") -> Dict[str, Any]:
    profiles = _bot_profiles(config, include_disabled=True)
    if not profiles:
        raise ClawBotError("没有可用的 ClawBot 配置")

    if bot_name:
        safe_name = _safe_bot_name(bot_name)
        for profile in profiles:
            if profile.get("name") == safe_name:
                return profile
        available = ", ".join(str(profile.get("name")) for profile in profiles)
        raise ClawBotError(f"未找到 ClawBot 配置：{bot_name}。可用配置：{available}")

    if len(profiles) == 1:
        return profiles[0]

    available = ", ".join(str(profile.get("name")) for profile in profiles)
    raise ClawBotError(f"配置了多个 ClawBot，请用 --bot 指定一个：{available}")


def _extract_first_url(text: str, downloader: Any) -> Optional[str]:
    text = text.strip()
    if not text:
        return None
    urls = downloader.extract_urls_from_text(text)
    if urls:
        return urls[0]
    match = re.search(r"https?://[^\s]+", text.replace("tp://", "http://"))
    if match:
        return match.group(0)
    if any(domain in text for domain in ("douyin.com", "kuaishou.com", "xhslink.com", "xiaohongshu.com")):
        match = re.search(r"([a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/[^\s]+)", text)
        if match:
            return f"https://{match.group(1)}"
    return None


def _format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.2f}{unit}"
        num /= 1024.0
    return f"{num:.2f}PB"


def _format_progress(data: Dict[str, Any]) -> Optional[str]:
    if data.get("status") != "downloading":
        return None
    filename = os.path.basename(data.get("filename") or "正在下载")
    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
    downloaded = data.get("downloaded_bytes") or 0
    speed = data.get("speed") or 0
    if total:
        percent = min(100.0, downloaded / total * 100)
        return (
            f"下载中：{filename}\n"
            f"进度：{percent:.1f}%\n"
            f"原始下载：{_format_bytes(downloaded)} / {_format_bytes(total)}\n"
            f"速度：{_format_bytes(speed)}/s"
        )
    return (
        f"下载中：{filename}\n"
        f"原始已下载：{_format_bytes(downloaded)}\n"
        f"速度：{_format_bytes(speed)}/s"
    )


def _format_download_error(platform: str, error: str) -> str:
    if "Fresh cookies" in error and platform == "douyin":
        return (
            "下载失败：抖音要求刷新 cookies。\n"
            "这个链接已经识别为抖音视频，但当前 douyin_cookies.txt 不够新，"
            "需要重新从已打开抖音的浏览器导出 cookies 后再试。"
        )
    if platform == "wechat_channels" and ("元宝 Web cookie" in error or "HTTP 401" in error):
        return (
            "下载失败：微信视频号本地解析需要你自己的元宝 Web cookie。\n"
            "请把元宝网页 cookie 保存到 cookies/wechat_channels_yuanbao_cookies.txt "
            "后重试；机器人默认不会使用第三方解析 API。"
        )
    return f"下载失败：{error or '未知错误'}"


def _is_video_file(path: Path) -> bool:
    mime_type = mimetypes.guess_type(str(path))[0] or ""
    return mime_type.startswith("video/") or path.suffix.lower() in VIDEO_SUFFIXES


def _ffprobe(path: Path) -> Dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return {}
    if result.returncode != 0:
        logger.warning("ffprobe returned %s for %s: %s", result.returncode, path, result.stderr.strip())
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _prepare_wechat_video_file(path: Path) -> tuple[Path, List[Path]]:
    if not _is_video_file(path):
        return path, []

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg not found; sending original video file: %s", path)
        return path, []

    probe = _ffprobe(path)
    streams = probe.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_codec = str(video_stream.get("codec_name") or "").lower()
    audio_codecs = {str(stream.get("codec_name") or "").lower() for stream in audio_streams}
    format_name = str((probe.get("format") or {}).get("format_name") or "").lower()
    pix_fmt = str(video_stream.get("pix_fmt") or "").lower()

    force_transcode = _parse_bool(os.getenv("WECHAT_FORCE_TRANSCODE_VIDEO"), default=False)
    crf = os.getenv("WECHAT_VIDEO_CRF", "20")
    timeout = _parse_int(os.getenv("WECHAT_VIDEO_TRANSCODE_TIMEOUT", "1800"), default=1800)
    output = path.with_name(f"{path.stem}.wechat.mp4")
    output.unlink(missing_ok=True)

    compatible_mp4 = "mp4" in format_name or "mov" in format_name
    compatible_video = video_codec == "h264" and pix_fmt in {"", "yuv420p"}
    compatible_audio = bool(audio_streams) and audio_codecs.issubset({"aac"})

    if not force_transcode and compatible_mp4 and compatible_video and compatible_audio:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        mode = "remux"
    elif audio_streams:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            crf,
            "-profile:v",
            "main",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        mode = "transcode"
    else:
        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            crf,
            "-profile:v",
            "main",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        mode = "transcode_with_silent_audio"

    try:
        logger.info("preparing WeChat-playable video via ffmpeg (%s): %s", mode, path)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except Exception as exc:
        logger.warning("video prepare failed for %s: %s", path, exc)
        output.unlink(missing_ok=True)
        return path, []

    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        logger.warning("video prepare returned %s for %s: %s", result.returncode, path, result.stderr.strip())
        output.unlink(missing_ok=True)
        return path, []

    logger.info("prepared WeChat-playable video: %s -> %s", path, output)
    return output, [output]


def _bilibili_cookies_available(downloader: Any) -> bool:
    cookie_path = getattr(downloader, "b_cookies_path", None)
    if not cookie_path:
        return False
    try:
        path = Path(str(cookie_path))
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _collect_result_files(result: Dict[str, Any], start_time: float, download_root: Path) -> List[Path]:
    candidates: List[Path] = []
    min_mtime = start_time - FILE_MTIME_TOLERANCE_SECONDS

    def add(value: Any) -> None:
        if not value:
            return
        path = Path(str(value))
        if path.exists() and path.is_file():
            candidates.append(path)

    add(result.get("full_path"))
    add(result.get("file_path"))
    add(result.get("path"))

    for item in result.get("files") or []:
        if isinstance(item, dict):
            add(item.get("full_path"))
            add(item.get("file_path"))
            add(item.get("path"))
            filename = item.get("filename")
            item_dir = item.get("download_path") or result.get("download_path")
            if filename and item_dir:
                add(Path(item_dir) / filename)

    if not candidates:
        download_path = Path(result.get("download_path") or "")
        if download_path.exists() and download_path.is_dir():
            for file_path in download_path.rglob("*"):
                try:
                    if file_path.is_file() and file_path.stat().st_mtime >= min_mtime:
                        candidates.append(file_path)
                except OSError:
                    continue

    if not candidates and download_root.exists():
        for file_path in download_root.rglob("*"):
            try:
                if file_path.is_file() and file_path.stat().st_mtime >= min_mtime:
                    candidates.append(file_path)
            except OSError:
                continue

    seen: set[str] = set()
    filtered: List[Path] = []
    ignored_suffixes = {".part", ".ytdl", ".aria2", ".tmp", ".enc"}
    for path in candidates:
        try:
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.suffix.lower() in ignored_suffixes:
                continue
            if path.name.startswith("."):
                continue
            if path.stat().st_size <= 0:
                continue
            filtered.append(path)
        except OSError:
            continue

    filtered.sort(key=lambda p: p.stat().st_mtime)
    return filtered


def _cleanup_empty_parent_dirs(file_path: Path, stop_at: Path) -> None:
    try:
        stop = stop_at.resolve()
        parent = file_path.parent.resolve()
    except OSError:
        return

    while parent != stop:
        try:
            parent.relative_to(stop)
            parent.rmdir()
        except (OSError, ValueError):
            break
        parent = parent.parent


class WeChatSaveXTubeBot:
    def __init__(
        self,
        client: ClawBotClient,
        downloader: Any,
        config: Dict[str, Any],
        wechat_config: Optional[Dict[str, Any]] = None,
        bot_name: str = "default",
        download_semaphore: Optional[asyncio.Semaphore] = None,
    ):
        self.client = client
        self.downloader = downloader
        self.config = config
        self.bot_name = bot_name
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stopping = asyncio.Event()
        self.tasks: set[asyncio.Task[Any]] = set()

        wechat_config = wechat_config or config.get("wechat") or {}
        allowed_raw = str(wechat_config.get("allowed_user_ids") or os.getenv("WECHAT_ALLOWED_USER_IDS", ""))
        self.allowed_users = _parse_allowed_users(allowed_raw)
        self.progress_interval = _parse_int(wechat_config.get("progress_interval") or os.getenv("WECHAT_PROGRESS_INTERVAL", "20"), default=20)
        self.max_send_files = _parse_int(wechat_config.get("max_send_files") or os.getenv("WECHAT_MAX_SEND_FILES", "20"), default=20)
        self.max_concurrent_downloads = _parse_int(
            wechat_config.get("max_concurrent_downloads") or os.getenv("WECHAT_MAX_CONCURRENT_DOWNLOADS", "1"),
            default=1,
        )
        self.download_semaphore = download_semaphore or asyncio.Semaphore(self.max_concurrent_downloads)
        self.cleanup_after_send = _parse_bool(
            wechat_config.get("cleanup_after_send") if "cleanup_after_send" in wechat_config else os.getenv("WECHAT_CLEANUP_AFTER_SEND"),
            default=True,
        )
        self.supported_platforms = _parse_supported_platforms(
            str(wechat_config.get("supported_platforms") or os.getenv("WECHAT_SUPPORTED_PLATFORMS", ""))
        )
        self.auto_playlist = str(wechat_config.get("bilibili_auto_playlist") or os.getenv("BILIBILI_AUTO_PLAYLIST", "false")).lower() in {"1", "true", "yes"}
        self.processed_message_ids: set[str] = set()
        self.processed_message_order: Deque[str] = deque()

    def _permitted(self, user_id: str) -> bool:
        return not self.allowed_users or user_id in self.allowed_users

    async def send_text(self, msg: WeChatInboundMessage, text: str) -> None:
        await asyncio.to_thread(self.client.send_text, msg.from_user_id, text, msg.context_token)

    async def send_file(self, msg: WeChatInboundMessage, file_path: Path, text: str = "") -> None:
        await asyncio.to_thread(self.client.send_media, msg.from_user_id, str(file_path), text, msg.context_token)

    async def _handle_download(self, msg: WeChatInboundMessage, url: str) -> None:
        platform = self.downloader.get_platform_name(url)
        if platform not in self.supported_platforms:
            await self.send_text(
                msg,
                "暂只支持这些平台：抖音、快手、微博、头条视频、小红书、B站、微信视频号。\n"
                f"当前识别为：{platform}",
            )
            return

        start_time = time.time()
        last_progress = {"time": 0.0, "text": ""}
        await self.send_text(msg, f"开始处理：{url}")
        if platform == "bilibili" and not _bilibili_cookies_available(self.downloader):
            await self.send_text(
                msg,
                "当前 B站未配置登录 cookies，只能下载未登录可见清晰度；"
                "如果视频支持更高清，一般需要导入 B站登录 cookies 后再下。",
            )

        def progress_callback(data: Any) -> None:
            if not isinstance(data, dict):
                return
            now = time.time()
            if now - last_progress["time"] < self.progress_interval:
                return
            text = _format_progress(data)
            if not text or text == last_progress["text"]:
                return
            last_progress.update({"time": now, "text": text})
            if self.loop:
                asyncio.run_coroutine_threadsafe(self.send_text(msg, text), self.loop)

        try:
            result = await self.downloader.download_video(
                url,
                progress_callback,
                self.auto_playlist,
                None,
                None,
                None,
            )
        except Exception as exc:
            logger.exception("download failed")
            await self.send_text(msg, f"下载失败：{exc}")
            return

        if not result or not (result.get("success") or result.get("status") == "success"):
            await self.send_text(msg, _format_download_error(platform, str((result or {}).get("error") or "")))
            return

        files = _collect_result_files(result, start_time, Path(self.downloader.download_path))
        if not files:
            await self.send_text(
                msg,
                "下载完成，但没有定位到可发送文件。\n"
                f"保存路径：{result.get('download_path') or result.get('full_path') or self.downloader.download_path}",
            )
            return

        title = result.get("title") or result.get("filename") or "下载完成"
        await self.send_text(msg, f"下载完成：{title}\n准备通过微信发送 {min(len(files), self.max_send_files)} 个文件。")

        sent = 0
        sent_files: List[Path] = []
        temp_files: List[Path] = []
        for file_path in files[: self.max_send_files]:
            try:
                original_size = file_path.stat().st_size
                send_path, prepared_temps = await asyncio.to_thread(_prepare_wechat_video_file, file_path)
                temp_files.extend(prepared_temps)
                send_size = send_path.stat().st_size
                if send_path.resolve() != file_path.resolve() or send_size != original_size:
                    send_text = (
                        f"发送视频（微信可播放版）：{send_path.name}\n"
                        f"原始文件：{_format_bytes(original_size)}\n"
                        f"发送文件：{_format_bytes(send_size)}"
                    )
                else:
                    send_text = f"发送视频：{send_path.name}\n文件大小：{_format_bytes(send_size)}"
                await self.send_file(msg, send_path, send_text)
                sent += 1
                sent_files.append(file_path)
            except Exception as exc:
                logger.exception("send file failed: %s", file_path)
                await self.send_text(msg, f"文件发送失败：{file_path.name}\n原因：{exc}\n本地路径：{file_path}")

        for file_path in temp_files:
            try:
                file_path.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("cleanup failed for prepared file %s: %s", file_path, exc)

        if self.cleanup_after_send and sent_files:
            deleted = 0
            for file_path in sent_files:
                try:
                    file_path.unlink(missing_ok=True)
                    _cleanup_empty_parent_dirs(file_path, Path(self.downloader.download_path))
                    deleted += 1
                except Exception as exc:
                    logger.warning("cleanup failed for %s: %s", file_path, exc)
            await self.send_text(msg, f"本地清理完成：已删除 {deleted} 个已回传文件。")

        remaining = len(files) - sent
        if remaining > 0:
            await self.send_text(msg, f"已发送 {sent} 个文件，剩余 {remaining} 个未发送。可调整 WECHAT_MAX_SEND_FILES。")

    def _remember_message(self, message_id: str) -> bool:
        if message_id in self.processed_message_ids:
            return False
        self.processed_message_ids.add(message_id)
        self.processed_message_order.append(message_id)
        while len(self.processed_message_order) > MAX_REMEMBERED_MESSAGES:
            expired = self.processed_message_order.popleft()
            self.processed_message_ids.discard(expired)
        return True

    async def handle_message(self, msg: WeChatInboundMessage) -> None:
        if not self._remember_message(msg.message_id):
            return

        if not self._permitted(msg.from_user_id):
            await self.send_text(msg, "你没有权限使用此下载机器人。")
            return

        text = msg.text.strip()
        lowered = text.lower()
        if lowered in {"/help", "help", "帮助"}:
            await self.send_text(
                msg,
                "发送抖音、快手、微博、头条、小红书、B站或微信视频号链接即可下载，并以微信文件形式回传。\n"
                "可用命令：/status 查看状态，/help 查看帮助。",
            )
            return
        if lowered == "/status":
            await self.send_text(
                msg,
                f"SaveXTube 微信版运行中。\n"
                f"ClawBot：{self.bot_name}\n"
                f"支持平台：抖音、快手、微博、头条视频、小红书、B站、微信视频号。\n"
                f"下载目录：{self.downloader.download_path}\n"
                f"并发下载：{self.max_concurrent_downloads}\n"
                f"本地文件：{'发送成功后删除' if self.cleanup_after_send else '发送成功后保留'}",
            )
            return
        if FINDER_FEED_CARD_MARKER in text:
            await self.send_text(
                msg,
                "微信视频号需要发送 `https://weixin.qq.com/sph/...` 分享短链。\n"
                "当前 ClawBot 不能稳定接收视频号小卡片；请在视频号里复制链接后发给我。",
            )
            return

        url = _extract_first_url(text, self.downloader)
        if not url:
            await self.send_text(msg, "请发送一个有效链接。视频号需要复制 `https://weixin.qq.com/sph/...` 分享短链，不要转发小卡片。")
            return
        if self.download_semaphore.locked():
            await self.send_text(msg, "当前已有下载任务在运行，本次请求已加入队列。")
        async with self.download_semaphore:
            await self._handle_download(msg, url)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.client.notify_start()
        logger.info("WeChat SaveXTube runner started: %s", self.bot_name)
        try:
            while not self.stopping.is_set():
                try:
                    raw_messages = await asyncio.to_thread(self.client.get_updates)
                except requests.exceptions.ReadTimeout:
                    continue
                except Exception as exc:
                    logger.warning("getupdates failed for %s: %s", self.bot_name, exc)
                    await asyncio.sleep(5)
                    continue

                for raw in raw_messages:
                    msg = self.client.parse_text_message(raw)
                    if not msg:
                        continue
                    task = asyncio.create_task(self.handle_message(msg))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
        finally:
            self.client.notify_stop()
            if self.tasks:
                await asyncio.wait(self.tasks, timeout=10)


def _build_downloader(config: Dict[str, Any]) -> Any:
    from wechat_downloader import WeChatVideoDownloader

    proxy_config = get_proxy_config(config) if config else {}
    proxy_host = proxy_config.get("proxy_host") or os.getenv("PROXY_HOST", "")
    if proxy_host:
        os.environ["PROXY_HOST"] = proxy_host
    channels_config = config.get("wechat_channels") or {}
    resolver_url = str(channels_config.get("resolver_url") or "").strip()
    if resolver_url and not os.getenv("WECHAT_CHANNELS_RESOLVER_URL"):
        os.environ["WECHAT_CHANNELS_RESOLVER_URL"] = resolver_url

    download_path = os.getenv("DOWNLOAD_PATH", "/downloads")
    cookies_base = os.getenv("COOKIES_PATH", "/app/cookies")
    Path(download_path).mkdir(parents=True, exist_ok=True)
    Path(cookies_base).mkdir(parents=True, exist_ok=True)
    return WeChatVideoDownloader(download_path, cookies_base, proxy_host)


def _build_client(config: Dict[str, Any], wechat: Optional[Dict[str, Any]] = None) -> ClawBotClient:
    wechat = wechat or config.get("wechat") or {}
    session_path = str(wechat.get("session_file") or _default_session_file(str(wechat.get("name") or "default")))
    client = ClawBotClient(
        session_path=session_path,
        base_url=str(wechat.get("base_url") or os.getenv("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")),
        cdn_base_url=str(wechat.get("cdn_base_url") or os.getenv("WECHAT_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c")),
        token=str(wechat.get("token") or os.getenv("WECHAT_BOT_TOKEN", "")),
        bot_agent=str(wechat.get("bot_agent") or os.getenv("WECHAT_BOT_AGENT", "SaveXTubeWeixin/1.0.0")),
        request_retries=_parse_int(wechat.get("request_retries") or os.getenv("WECHAT_REQUEST_RETRIES", "2"), default=2, minimum=0),
        retry_backoff=_parse_float(wechat.get("retry_backoff") or os.getenv("WECHAT_REQUEST_RETRY_BACKOFF", "1.0"), default=1.0, minimum=0.1),
    )
    if not client.token:
        client.load_session()
    return client


def _shared_download_semaphore(config: Dict[str, Any], profiles: Sequence[Dict[str, Any]]) -> asyncio.Semaphore:
    wechat = config.get("wechat") or {}
    raw = wechat.get("max_concurrent_downloads") or os.getenv("WECHAT_MAX_CONCURRENT_DOWNLOADS", "1")
    if not raw and profiles:
        raw = profiles[0].get("max_concurrent_downloads")
    return asyncio.Semaphore(_parse_int(raw, default=1))


def _config_reload_interval(config: Dict[str, Any]) -> int:
    wechat = config.get("wechat") or {}
    return _parse_int(
        wechat.get("config_reload_interval") or os.getenv("WECHAT_CONFIG_RELOAD_INTERVAL", "15"),
        default=15,
    )


def _running_bot_session_file(record: Dict[str, Any]) -> str:
    return str(record.get("session_file") or "")


async def _stop_running_bot(name: str, record: Dict[str, Any]) -> None:
    bot = record.get("bot")
    task = record.get("task")
    if bot:
        bot.stopping.set()
    if isinstance(task, asyncio.Task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("ClawBot %s stopped with error: %s", name, exc)


async def _start_profile_bot(
    config: Dict[str, Any],
    profile: Dict[str, Any],
    downloader: Any,
    shared_semaphore: asyncio.Semaphore,
) -> Optional[Dict[str, Any]]:
    bot_name = str(profile.get("name") or "default")
    client = _build_client(config, profile)
    if not client.configured:
        if bot_name not in PENDING_LOGIN_WARNED:
            logger.warning(
                "ClawBot %s not logged in yet; waiting for session file: %s",
                bot_name,
                profile.get("session_file"),
            )
            PENDING_LOGIN_WARNED.add(bot_name)
        return None

    PENDING_LOGIN_WARNED.discard(bot_name)
    bot = WeChatSaveXTubeBot(
        client,
        downloader,
        config,
        wechat_config=profile,
        bot_name=bot_name,
        download_semaphore=shared_semaphore,
    )
    task = asyncio.create_task(bot.run(), name=f"savextube-wechat-{bot_name}")
    logger.info("ClawBot profile started: %s", bot_name)
    return {
        "bot": bot,
        "task": task,
        "session_file": str(profile.get("session_file") or ""),
    }


async def _reconcile_bots(
    config: Dict[str, Any],
    downloader: Any,
    shared_semaphore: asyncio.Semaphore,
    running: Dict[str, Dict[str, Any]],
) -> None:
    profiles = _bot_profiles(config)
    desired = {str(profile.get("name") or "default"): profile for profile in profiles}

    for name, record in list(running.items()):
        profile = desired.get(name)
        task = record.get("task")
        if isinstance(task, asyncio.Task) and task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("ClawBot profile exited unexpectedly: %s: %s", name, exc)
            running.pop(name, None)
            continue
        if not profile or str(profile.get("session_file") or "") != _running_bot_session_file(record):
            logger.info("ClawBot profile stopped: %s", name)
            running.pop(name, None)
            await _stop_running_bot(name, record)

    for name, profile in desired.items():
        if name in running:
            continue
        record = await _start_profile_bot(config, profile, downloader, shared_semaphore)
        if record:
            running[name] = record


async def _run_bot(config: Optional[Dict[str, Any]] = None) -> None:
    if config is None:
        config = _load_config()
        _configure_logging(config)

    config_path = _first_existing_config_path()
    downloader = _build_downloader(config)
    profiles = _bot_profiles(config)
    shared_semaphore = _shared_download_semaphore(config, profiles)
    running: Dict[str, Dict[str, Any]] = {}
    stopping = asyncio.Event()

    loop = asyncio.get_running_loop()

    def stop_all() -> None:
        stopping.set()
        for record in running.values():
            bot = record.get("bot")
            if bot:
                bot.stopping.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_all)
        except NotImplementedError:
            pass

    reload_interval = _config_reload_interval(config)
    await _reconcile_bots(config, downloader, shared_semaphore, running)
    logger.info("config hot reload enabled: %ss", reload_interval)

    try:
        while not stopping.is_set():
            try:
                await asyncio.wait_for(stopping.wait(), timeout=reload_interval)
                break
            except asyncio.TimeoutError:
                pass

            latest_config = load_toml_config(config_path) if Path(config_path).exists() else config
            if latest_config:
                config = latest_config
            await _reconcile_bots(config, downloader, shared_semaphore, running)
    finally:
        await asyncio.gather(
            *(_stop_running_bot(name, record) for name, record in list(running.items())),
            return_exceptions=True,
        )


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="SaveXTube WeChat ClawBot runner")
    sub = parser.add_subparsers(dest="command", required=True)
    login_parser = sub.add_parser("login", help="scan QR and save WeChat ClawBot session")
    login_parser.add_argument("--timeout", type=int, default=480)
    login_parser.add_argument("--bot", default="", help="bot name from [[wechat.bots]]")
    sub.add_parser("run", help="run WeChat-only downloader")
    args = parser.parse_args(argv)

    config = _load_config()
    _configure_logging(config)
    if args.command == "login":
        profile = _resolve_bot_profile(config, args.bot)
        client = _build_client(config, profile)
        client.login_with_qr(timeout_seconds=args.timeout)
        return
    if args.command == "run":
        asyncio.run(_run_bot(config))


if __name__ == "__main__":
    main()
