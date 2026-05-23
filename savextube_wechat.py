#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WeChat-only SaveXTube entrypoint.

Run modes:
  python3 savextube_wechat.py login
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import requests

os.environ.setdefault("SAVEXTUBE_WECHAT_ONLY", "1")

from clawbot_wechat import ClawBotClient, ClawBotError, WeChatInboundMessage  # noqa: E402
from config_reader import get_proxy_config, load_toml_config  # noqa: E402

logger = logging.getLogger("savextube.wechat_runner")

DEFAULT_SUPPORTED_PLATFORMS = {"douyin", "kuaishou", "weibo", "toutiao", "xiaohongshu", "bilibili"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".flv", ".avi", ".ts"}


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


def _config_value(config: Dict[str, Any], section: str, key: str, env_key: str, default: Any = "") -> Any:
    value = (config.get(section) or {}).get(key)
    if value is None or value == "":
        value = os.getenv(env_key, default)
    return value


def _parse_allowed_users(raw: str) -> set[str]:
    if not raw:
        return set()
    return {part.strip() for part in re.split(r"[,;\s]+", raw) if part.strip()}


def _parse_supported_platforms(raw: str) -> set[str]:
    if not raw:
        return set(DEFAULT_SUPPORTED_PLATFORMS)
    values = {part.strip().lower() for part in re.split(r"[,;\s]+", raw) if part.strip()}
    return values or set(DEFAULT_SUPPORTED_PLATFORMS)


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
            f"大小：{_format_bytes(downloaded)} / {_format_bytes(total)}\n"
            f"速度：{_format_bytes(speed)}/s"
        )
    return (
        f"下载中：{filename}\n"
        f"已下载：{_format_bytes(downloaded)}\n"
        f"速度：{_format_bytes(speed)}/s"
    )


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

    force_transcode = os.getenv("WECHAT_FORCE_TRANSCODE_VIDEO", "true").lower() in {"1", "true", "yes"}
    crf = os.getenv("WECHAT_VIDEO_CRF", "20")
    timeout = int(os.getenv("WECHAT_VIDEO_TRANSCODE_TIMEOUT", "1800"))
    output = path.with_name(f"{path.stem}.wechat.mp4")
    output.unlink(missing_ok=True)

    compatible_mp4 = "mp4" in format_name or "mov" in format_name
    compatible_video = video_codec == "h264" and pix_fmt in {"", "yuv420p"}
    compatible_audio = not audio_codecs or audio_codecs.issubset({"aac", "mp3"})

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

    download_path = Path(result.get("download_path") or "")
    if download_path.exists() and download_path.is_dir():
        for file_path in download_path.rglob("*"):
            if file_path.is_file():
                candidates.append(file_path)

    if not candidates and download_root.exists():
        for file_path in download_root.rglob("*"):
            if file_path.is_file() and file_path.stat().st_mtime >= start_time - 2:
                candidates.append(file_path)

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
            if path.stat().st_mtime < start_time - 2 and not result.get("full_path"):
                continue
            filtered.append(path)
        except OSError:
            continue

    filtered.sort(key=lambda p: p.stat().st_mtime)
    return filtered


class WeChatSaveXTubeBot:
    def __init__(self, client: ClawBotClient, downloader: Any, config: Dict[str, Any]):
        self.client = client
        self.downloader = downloader
        self.config = config
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.stopping = asyncio.Event()
        self.tasks: set[asyncio.Task[Any]] = set()

        wechat_config = config.get("wechat") or {}
        allowed_raw = str(wechat_config.get("allowed_user_ids") or os.getenv("WECHAT_ALLOWED_USER_IDS", ""))
        self.allowed_users = _parse_allowed_users(allowed_raw)
        self.progress_interval = int(wechat_config.get("progress_interval") or os.getenv("WECHAT_PROGRESS_INTERVAL", "20"))
        self.max_send_files = int(wechat_config.get("max_send_files") or os.getenv("WECHAT_MAX_SEND_FILES", "20"))
        self.cleanup_after_send = str(wechat_config.get("cleanup_after_send") if "cleanup_after_send" in wechat_config else os.getenv("WECHAT_CLEANUP_AFTER_SEND", "true")).lower() in {"1", "true", "yes"}
        self.supported_platforms = _parse_supported_platforms(
            str(wechat_config.get("supported_platforms") or os.getenv("WECHAT_SUPPORTED_PLATFORMS", ""))
        )
        self.auto_playlist = str(wechat_config.get("bilibili_auto_playlist") or os.getenv("BILIBILI_AUTO_PLAYLIST", "false")).lower() in {"1", "true", "yes"}
        self.processed_message_ids: set[str] = set()

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
                "暂只支持这些平台：抖音、快手、微博、头条视频、小红书、B站。\n"
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
            await self.send_text(msg, f"下载失败：{(result or {}).get('error', '未知错误')}")
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
                send_path, prepared_temps = await asyncio.to_thread(_prepare_wechat_video_file, file_path)
                temp_files.extend(prepared_temps)
                size = _format_bytes(send_path.stat().st_size)
                await self.send_file(msg, send_path, f"发送视频：{send_path.name}\n大小：{size}")
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
                    deleted += 1
                except Exception as exc:
                    logger.warning("cleanup failed for %s: %s", file_path, exc)
            await self.send_text(msg, f"本地清理完成：已删除 {deleted} 个已回传文件。")

        remaining = len(files) - sent
        if remaining > 0:
            await self.send_text(msg, f"已发送 {sent} 个文件，剩余 {remaining} 个未发送。可调整 WECHAT_MAX_SEND_FILES。")

    async def handle_message(self, msg: WeChatInboundMessage) -> None:
        if msg.message_id in self.processed_message_ids:
            return
        self.processed_message_ids.add(msg.message_id)
        if len(self.processed_message_ids) > 1000:
            self.processed_message_ids = set(list(self.processed_message_ids)[-500:])

        if not self._permitted(msg.from_user_id):
            await self.send_text(msg, "你没有权限使用此下载机器人。")
            return

        text = msg.text.strip()
        lowered = text.lower()
        if lowered in {"/help", "help", "帮助"}:
            await self.send_text(
                msg,
                "发送抖音、快手、微博、头条、小红书或 B站视频链接即可下载，并以微信文件形式回传。\n"
                "可用命令：/status 查看状态，/help 查看帮助。",
            )
            return
        if lowered == "/status":
            await self.send_text(
                msg,
                f"SaveXTube 微信版运行中。\n"
                f"支持平台：抖音、快手、微博、头条视频、小红书、B站。\n"
                f"下载目录：{self.downloader.download_path}",
            )
            return

        url = _extract_first_url(text, self.downloader)
        if not url:
            await self.send_text(msg, "请发送一个有效链接。")
            return
        await self._handle_download(msg, url)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.client.notify_start()
        logger.info("WeChat SaveXTube runner started")
        try:
            while not self.stopping.is_set():
                try:
                    raw_messages = await asyncio.to_thread(self.client.get_updates)
                except requests.exceptions.ReadTimeout:
                    continue
                except Exception as exc:
                    logger.warning("getupdates failed: %s", exc)
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

    download_path = os.getenv("DOWNLOAD_PATH", "/downloads")
    cookies_base = os.getenv("COOKIES_PATH", "/app/cookies")
    Path(download_path).mkdir(parents=True, exist_ok=True)
    Path(cookies_base).mkdir(parents=True, exist_ok=True)
    return WeChatVideoDownloader(download_path, cookies_base, proxy_host)


def _build_client(config: Dict[str, Any]) -> ClawBotClient:
    wechat = config.get("wechat") or {}
    session_path = str(wechat.get("session_file") or os.getenv("WECHAT_SESSION_FILE", "/app/config/wechat_session.json"))
    client = ClawBotClient(
        session_path=session_path,
        base_url=str(wechat.get("base_url") or os.getenv("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")),
        cdn_base_url=str(wechat.get("cdn_base_url") or os.getenv("WECHAT_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c")),
        token=str(wechat.get("token") or os.getenv("WECHAT_BOT_TOKEN", "")),
        bot_agent=str(wechat.get("bot_agent") or os.getenv("WECHAT_BOT_AGENT", "SaveXTubeWeixin/1.0.0")),
    )
    if not client.token:
        client.load_session()
    return client


async def _run_bot() -> None:
    config = _load_config()
    client = _build_client(config)
    if not client.configured:
        raise ClawBotError("微信未登录。请先执行：python3 savextube_wechat.py login")
    downloader = _build_downloader(config)
    bot = WeChatSaveXTubeBot(client, downloader, config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, bot.stopping.set)
        except NotImplementedError:
            pass
    await bot.run()


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="SaveXTube WeChat ClawBot runner")
    sub = parser.add_subparsers(dest="command", required=True)
    login_parser = sub.add_parser("login", help="scan QR and save WeChat ClawBot session")
    login_parser.add_argument("--timeout", type=int, default=480)
    sub.add_parser("run", help="run WeChat-only downloader")
    args = parser.parse_args(argv)

    config = _load_config()
    client = _build_client(config)
    if args.command == "login":
        client.login_with_qr(timeout_seconds=args.timeout)
        return
    if args.command == "run":
        asyncio.run(_run_bot())


if __name__ == "__main__":
    main()
