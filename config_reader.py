#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal TOML config reader for the WeChat-only runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


def load_toml_config(config_path: str = "/app/config/savextube.toml") -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def get_proxy_config(config: Dict[str, Any]) -> Dict[str, Any]:
    proxy = config.get("proxy") or {}
    return {
        "proxy_host": proxy.get("proxy_host") or os.getenv("PROXY_HOST", ""),
        "enabled": bool(proxy.get("proxy_host") or os.getenv("PROXY_HOST")),
    }


def get_logging_config(config: Dict[str, Any]) -> Dict[str, Any]:
    logging_config = config.get("logging") or {}
    return {
        "log_level": logging_config.get("log_level", "INFO"),
        "log_dir": logging_config.get("log_dir", "/app/logs"),
        "log_max_size": logging_config.get("log_max_size", 10),
        "log_backup_count": logging_config.get("log_backup_count", 5),
        "log_to_console": logging_config.get("log_to_console", True),
        "log_to_file": logging_config.get("log_to_file", True),
    }


def get_bilibili_config(config: Dict[str, Any]) -> Dict[str, Any]:
    bilibili = config.get("bilibili") or {}
    return {
        "poll_interval": bilibili.get("bilibili_poll_interval", 1),
    }


def print_config_summary(config: Dict[str, Any]) -> None:
    return None
