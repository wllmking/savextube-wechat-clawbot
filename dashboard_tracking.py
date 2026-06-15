#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard tracking and statistics storage."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

DB_PATH = Path(os.getenv("DASHBOARD_DB_PATH", "/app/db/dashboard.db"))
_DB_LOCK = threading.RLock()

CREATE_DOWNLOADS_TABLE = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    user_id TEXT,
    url TEXT,
    platform TEXT,
    title TEXT,
    status TEXT,
    error TEXT,
    filesize INTEGER,
    download_path TEXT,
    files TEXT,
    progress REAL DEFAULT 0,
    speed REAL DEFAULT 0,
    detail TEXT,
    success INTEGER DEFAULT 0,
    source TEXT
);
"""

def _connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _ensure_tables() -> None:
    with _DB_LOCK:
        conn = _connection()
        try:
            conn.execute(CREATE_DOWNLOADS_TABLE)
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(downloads)").fetchall()}
            missing = [
                ("progress", "REAL DEFAULT 0"),
                ("speed", "REAL DEFAULT 0"),
                ("detail", "TEXT"),
            ]
            for name, definition in missing:
                if name not in existing:
                    conn.execute(f"ALTER TABLE downloads ADD COLUMN {name} {definition}")
            conn.commit()
        finally:
            conn.close()

_ensure_tables()

def _execute(query: str, params: Sequence[Any] = ()) -> Any:
    with _DB_LOCK:
        conn = _connection()
        try:
            cur = conn.execute(query, params)
            if query.strip().split()[0].upper() == "SELECT":
                rows = cur.fetchall()
                return rows
            conn.commit()
            return cur
        finally:
            conn.close()

def record_download_job(
    url: str,
    platform: str,
    user_id: str = "",
    title: str = "",
    source: str = "dashboard",
) -> int:
    created_at = time.time()
    with _DB_LOCK:
        conn = _connection()
        try:
            cur = conn.execute(
                "INSERT INTO downloads (created_at, user_id, url, platform, title, status, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (created_at, user_id, url, platform, title, "pending", source),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

def update_download_job(task_id: int, **fields: Any) -> None:
    if not fields:
        return
    keys = []
    values = []
    for key, value in fields.items():
        keys.append(f"{key} = ?")
        if isinstance(value, (dict, list)):
            values.append(json.dumps(value, ensure_ascii=False))
        else:
            values.append(value)
    values.append(task_id)
    query = f"UPDATE downloads SET {', '.join(keys)} WHERE id = ?"
    _execute(query, values)


def _parse_files(raw_files):
    if not raw_files:
        return []
    if isinstance(raw_files, list):
        return raw_files
    if isinstance(raw_files, str):
        try:
            parsed = json.loads(raw_files)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []

def record_download_result(
    url: str,
    platform: str,
    title: str,
    status: str,
    success: bool,
    filesize: int = 0,
    download_path: str = "",
    files: Optional[List[Dict[str, Any]]] = None,
    error: str = "",
    user_id: str = "",
    source: str = "wechat",
) -> int:
    now = time.time()
    _ensure_tables()
    files_json = files or []
    with _DB_LOCK:
        conn = _connection()
        try:
            cur = conn.execute(
                "INSERT INTO downloads (created_at, started_at, finished_at, user_id, url, platform, title, status, error, filesize, download_path, files, success, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now,
                    now,
                    now,
                    user_id,
                    url,
                    platform,
                    title,
                    status,
                    error,
                    filesize,
                    download_path,
                    json.dumps(files_json, ensure_ascii=False),
                    1 if success else 0,
                    source,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

def list_downloads(limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    params: List[Any] = []
    query = "SELECT * FROM downloads"
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = _execute(query, params)
    return [dict(row) for row in rows]

def get_statistics(days: int = 30) -> Dict[str, Any]:
    since = time.time() - days * 86400
    query = "SELECT status, success, platform, filesize, created_at FROM downloads WHERE created_at >= ?"
    rows = _execute(query, (since,))
    total = len(rows)
    success = sum(1 for row in rows if row["success"] == 1)
    failed = total - success
    total_size = sum(row["filesize"] or 0 for row in rows)
    by_platform: Dict[str, int] = {}
    trends: Dict[str, int] = {}
    media_counts = {"video": 0, "audio": 0, "image": 0}
    for row in rows:
        platform = row["platform"] or "unknown"
        by_platform[platform] = by_platform.get(platform, 0) + 1
        day = datetime.fromtimestamp(row["created_at"]).strftime("%Y-%m-%d")
        trends[day] = trends.get(day, 0) + 1
        for file_item in _parse_files(row["files"]):
            ext = str(file_item.get("ext") or file_item.get("format") or "").lower()
            if any(token in ext for token in ("mp4", "mkv", "mov", "flv", "webm", "avi")):
                media_counts["video"] += 1
            elif any(token in ext for token in ("mp3", "m4a", "aac", "wav", "flac", "ogg")):
                media_counts["audio"] += 1
            elif any(token in ext for token in ("jpg", "jpeg", "png", "gif", "bmp")):
                media_counts["image"] += 1
    chart = [
        {"date": (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"), "count": trends.get((datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d"), 0)}
        for i in reversed(range(days if days <= 30 else 30))
    ]
    return {
        "total": total,
        "success": success,
        "failed": failed,
        "total_size": total_size,
        "by_platform": by_platform,
        "trend": chart,
        "media_counts": media_counts,
    }

def load_recent_history(limit: int = 100) -> List[Dict[str, Any]]:
    rows = _execute("SELECT * FROM downloads ORDER BY created_at DESC LIMIT ?", (limit,))
    return [dict(row) for row in rows]

def load_task(task_id: int) -> Optional[Dict[str, Any]]:
    rows = _execute("SELECT * FROM downloads WHERE id = ?", (task_id,))
    if not rows:
        return None
    return dict(rows[0])
