#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deployment sanity check for SaveXTube WeChat ClawBot."""

from __future__ import annotations

import json
import os
import argparse
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "savextube.toml"
REQUIRED_DIRS = [ROOT / "config", ROOT / "cookies", ROOT / "downloads", ROOT / "logs", ROOT / "db"]
COOKIE_FILES = {
    "bilibili": "cookies/bilibili_cookies.txt",
    "douyin": "cookies/douyin_cookies.txt",
    "kuaishou": "cookies/kuaishou_cookies.txt",
    "weibo": "cookies/weibo_cookies.txt",
    "toutiao": "cookies/toutiao_cookies.txt",
    "xiaohongshu": "cookies/xiaohongshu_cookies.txt",
    "wechat_channels": "cookies/wechat_channels_yuanbao_cookies.txt",
}


def load_toml_config(path: Path) -> Dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except Exception as exc:
        raise RuntimeError(f"failed to parse TOML config {path}: {exc}") from exc


def check_executable(name: str) -> bool:
    return shutil.which(name) is not None


def check_file_mode(path: Path, expected: int) -> bool:
    try:
        mode = path.stat().st_mode & 0o777
        return mode == expected
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Deployment sanity check for SaveXTube WeChat ClawBot")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出 JSON 结构，便于 CI/脚本消费")
    args = parser.parse_args()

    errors: List[str] = []
    warnings: List[str] = []

    config_path = Path(os.getenv("SAVEXTUBE_CONFIG", "")) if os.getenv("SAVEXTUBE_CONFIG") else DEFAULT_CONFIG
    print(f"Checking repository root: {ROOT}")
    print(f"Using config path: {config_path}")

    config: Dict[str, Any] = {}
    if not config_path.exists():
        errors.append(f"配置文件不存在: {config_path}")
    else:
        try:
            config = load_toml_config(config_path)
            print(f"Loaded config: {config_path}")
        except Exception as exc:
            errors.append(str(exc))
            config = {}

    for directory in REQUIRED_DIRS:
        if not directory.exists():
            warnings.append(f"缺少目录: {directory}（建议创建并映射到宿主机）")
        elif not directory.is_dir():
            errors.append(f"路径不是目录: {directory}")

    session_files = []
    if config:
        wechat = config.get("wechat") or {}
        bots = wechat.get("bots") or []
        if isinstance(bots, list) and bots:
            for profile in bots:
                if not isinstance(profile, dict):
                    continue
                session_files.append(Path(str(profile.get("session_file") or "")))
        else:
            session_files.append(Path(str(wechat.get("session_file") or "/app/config/wechat_session.json")))

    for session_file in filter(lambda p: p and p.parts, session_files):
        if not session_file.exists():
            warnings.append(f"ClawBot session 文件不存在: {session_file}（第一次登录后会生成）")
        else:
            try:
                text = session_file.read_text(encoding="utf-8")
                data = json.loads(text)
                if not data.get("token"):
                    warnings.append(f"session 文件缺少 token: {session_file}")
            except Exception as exc:
                errors.append(f"无法读取 session 文件 {session_file}: {exc}")
            if not check_file_mode(session_file, 0o600):
                warnings.append(f"建议将 session 文件权限设置为 600: {session_file}")

    proxy = config.get("proxy") or {}
    if proxy.get("proxy_host"):
        print(f"Proxy configured: {proxy.get('proxy_host')}")

    supported = str((config.get("wechat") or {}).get("supported_platforms") or "").lower()
    if supported:
        for platform, cookie_path in COOKIE_FILES.items():
            if platform in supported and platform != "wechat_channels" and not Path(ROOT / cookie_path).exists():
                warnings.append(f"未检测到 {platform} 登录 cookie 文件: {cookie_path}（会影响解析成功率）")
        if "wechat_channels" in supported and not Path(ROOT / COOKIE_FILES["wechat_channels"]).exists() and not os.getenv("WECHAT_CHANNELS_YUANBAO_COOKIE"):
            warnings.append(
                "未检测到微信视频号元宝 cookie，也未设置 WECHAT_CHANNELS_YUANBAO_COOKIE; 视频号本地解析可能失败"
            )

    if not check_executable("ffmpeg"):
        warnings.append("系统找不到 ffmpeg；视频整理或转码功能将不可用")
    if not check_executable("ffprobe"):
        warnings.append("系统找不到 ffprobe；视频格式检测功能将不可用")

    if not check_executable("docker"):
        warnings.append("未检测到 docker，可选但推荐用于容器部署")
    if not check_executable("docker-compose") and not check_executable("docker"):
        warnings.append("未检测到 docker-compose。请确保 Docker 与 Docker Compose 可用，或使用 docker compose 命令")

    result = {
        "errors": errors,
        "warnings": warnings,
        "ok": not errors and not warnings,
    }

    if args.as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print("\n检查结果:")
        for message in errors:
            print(f"[ERROR] {message}")
        for message in warnings:
            print(f"[WARN] {message}")
        if result["ok"]:
            print("[OK] 通过所有部署检查。")
        elif not errors:
            print("[OK] 通过关键检查，但存在一些建议性警告。")
        else:
            print("[FAIL] 存在关键问题，请修复后再部署。")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
