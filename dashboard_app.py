#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard backend for SaveXTube WeChat runtime."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import (
    Flask,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config_reader import load_toml_config
from dashboard_tracking import (
    get_statistics,
    list_downloads,
    load_recent_history,
    load_task,
    record_download_job,
    update_download_job,
)
from wechat_downloader import WeChatVideoDownloader

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = Flask(__name__, static_folder="static", template_folder="templates")
if not hasattr(app, "before_first_request"):
    def _dummy_before_first_request(func):
        return func
    app.before_first_request = _dummy_before_first_request
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme123")

ADMIN_USER = os.getenv("DASHBOARD_ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_ADMIN_PASS", "admin")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "3196"))
DOWNLOAD_PATH = os.getenv("DOWNLOAD_PATH", "/downloads")
COOKIES_PATH = os.getenv("COOKIES_PATH", "/app/cookies")
PROXY_HOST = os.getenv("PROXY_HOST", "")
LOG_DIR = Path(os.getenv("LOG_DIR", "/app/logs"))
LOG_FILE = LOG_DIR / "savextube-wechat.log"
CONFIG_PATH = Path(os.getenv("SAVEXTUBE_CONFIG", "/app/config/savextube.toml"))

TASK_QUEUE: queue.Queue[int] = queue.Queue()
TASK_WORKER_STARTED = threading.Event()


def login_required(func):
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    wrapper.__name__ = func.__name__
    return wrapper


@app.route("/")
def root() -> Any:
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET"])
def login() -> Any:
    return render_template("login.html")


@app.route("/api/login", methods=["POST"])
def api_login() -> Any:
    data = request.json or {}
    username = data.get("username", "")
    password = data.get("password", "")
    if username == ADMIN_USER and password == ADMIN_PASS:
        session["logged_in"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "用户名或密码错误"}), 401


@app.route("/logout")
def logout() -> Any:
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard() -> Any:
    return render_template("dashboard.html")


@app.route("/api/statistics")
@login_required
def api_statistics() -> Any:
    days = int(request.args.get("days", 30))
    stats = get_statistics(days=days)
    return jsonify(stats)


@app.route("/api/downloads")
@login_required
def api_downloads() -> Any:
    status = request.args.get("status")
    tasks = list_downloads(limit=200, status=status)
    return jsonify({"tasks": tasks})


@app.route("/api/history")
@login_required
def api_history() -> Any:
    limit = int(request.args.get("limit", 200))
    history = load_recent_history(limit)
    return jsonify({"history": history})


@app.route("/api/logs")
@login_required
def api_logs() -> Any:
    lines = []
    if LOG_FILE.exists():
        try:
            with LOG_FILE.open("r", encoding="utf-8", errors="ignore") as handle:
                lines = handle.readlines()[-300:]
        except Exception:
            lines = ["无法读取日志文件。"]
    return jsonify({"logs": [line.rstrip("\n") for line in lines]})


@app.route("/api/settings")
@login_required
def api_settings() -> Any:
    config = {}
    if CONFIG_PATH.exists():
        try:
            config = load_toml_config(str(CONFIG_PATH))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
    return jsonify({"config": config})


@app.route("/api/submit_download", methods=["POST"])
@login_required
def api_submit_download() -> Any:
    data = request.json or {}
    url = data.get("url", "").strip()
    platform = data.get("platform", "").strip()
    if not url:
        return jsonify({"success": False, "message": "请输入下载链接"}), 400
    task_id = record_download_job(url=url, platform=platform or "auto", title="", user_id="admin", source="dashboard")
    TASK_QUEUE.put(task_id)
    return jsonify({"success": True, "task_id": task_id})


def _load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        return load_toml_config(str(CONFIG_PATH))
    return {}


def _download_task(task_id: int) -> None:
    try:
        record = list_downloads(limit=1, status=None)
    except Exception:
        return

    conn = None
    task = None
    try:
        task = load_task(task_id)
        if not task:
            return

        update_download_job(task_id, status="running", started_at=time.time(), detail="正在下载")
        downloader = WeChatVideoDownloader(DOWNLOAD_PATH, COOKIES_PATH, PROXY_HOST)

        def progress_callback(data: Dict[str, Any]) -> None:
            if not isinstance(data, dict):
                return
            progress = float(data.get("progress") or 0)
            speed = float(data.get("speed") or 0)
            update_download_job(task_id, progress=progress, speed=speed, detail="下载中")

        import asyncio

        result = asyncio.run(downloader.download_video(url=task["url"], progress_callback=progress_callback, auto_playlist=False))
        result = result if isinstance(result, dict) else {}
        success = bool(result.get("success") or result.get("status") == "success")
        status = "success" if success else "failed"
        size = 0
        if result.get("files"):
            for item in result["files"]:
                try:
                    size += int(item.get("size") or 0)
                except Exception:
                    pass
        update_download_job(
            task_id,
            status=status,
            finished_at=time.time(),
            success=1 if success else 0,
            filesize=size,
            detail=result.get("error") or ("完成" if success else "失败"),
            download_path=str(DOWNLOAD_PATH),
            files=result.get("files") or [],
        )
    except Exception as exc:
        update_download_job(task_id, status="failed", finished_at=time.time(), detail=str(exc))


def _task_worker() -> None:
    while True:
        task_id = TASK_QUEUE.get()
        if task_id is None:
            break
        try:
            _download_task(task_id)
        finally:
            TASK_QUEUE.task_done()


def start_task_worker() -> None:
    if TASK_WORKER_STARTED.is_set():
        return
    TASK_WORKER_STARTED.set()
    thread = threading.Thread(target=_task_worker, daemon=True)
    thread.start()


@app.before_first_request
def on_startup() -> None:
    start_task_worker()


if __name__ == "__main__":
    start_task_worker()
    app.run(host="0.0.0.0", port=DASHBOARD_PORT)
