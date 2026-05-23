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
