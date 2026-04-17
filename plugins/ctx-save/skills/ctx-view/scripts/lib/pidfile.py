#!/usr/bin/env python3
"""PID file 管理模組。

負責讀寫、驗證、刪除 ctx-viewer 背景服務的 PID file。

檔案格式：JSON
    {
        "pid": int,
        "port": int,
        "db": str (absolute path),
        "started_at": str (ISO8601, UTC)
    }

路徑解析規則：
    1. 若環境變數 XDG_CACHE_HOME 有設定 → $XDG_CACHE_HOME/ctx-viewer/viewer.pid
    2. 否則 → $HOME/.cache/ctx-viewer/viewer.pid

依循 XDG Base Directory Specification。POSIX 平台限定（Windows 不支援）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def pidfile_path() -> Path:
    """解析 XDG-compliant 的 PID file 絕對路徑。

    Returns:
        Path: PID file 的絕對路徑。父目錄可能尚未建立。
    """
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        base = Path(xdg_cache)
    else:
        base = Path.home() / ".cache"
    return base / "ctx-viewer" / "viewer.pid"


def read_pidfile() -> Optional[dict]:
    """讀取並解析 PID file。

    Returns:
        dict | None:
            - 成功解析 → {"pid": int, "port": int, "db": str, "started_at": str}
            - 檔案不存在、格式錯誤、關鍵欄位缺失 → None
    """
    path = pidfile_path()
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None

    # 驗證必要欄位與型別
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    port = data.get("port")
    db = data.get("db")
    if not isinstance(pid, int) or not isinstance(port, int) or not isinstance(db, str):
        return None

    return data


def write_pidfile(pid: int, port: int, db: str) -> None:
    """以原子方式寫入 PID file。

    流程：
        1. 建立父目錄（若不存在）
        2. 寫入同目錄的 `.tmp` 暫存檔
        3. `os.replace` 覆蓋正式檔案（POSIX 下為原子操作）

    Args:
        pid: 背景 viewer 程序的 PID。
        port: viewer 監聽的 TCP port。
        db: context.db 的絕對路徑字串。
    """
    path = pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "pid": pid,
        "port": port,
        "db": db,
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_path), str(path))
    finally:
        # 若 replace 失敗，盡量清理暫存檔
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def clear_pidfile() -> None:
    """刪除 PID file。不存在時靜默忽略。"""
    path = pidfile_path()
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        # 其他 I/O 錯誤亦視為最大努力（best effort）
        return


def is_alive(pid: int) -> bool:
    """檢查指定 PID 是否仍存活（POSIX）。

    使用 ``os.kill(pid, 0)`` — 不送訊號，僅觸發權限檢查：
        - 成功 → 程序存在且可發訊號（True）
        - ProcessLookupError → 程序不存在（False）
        - PermissionError → 程序存在但非同一使用者（仍視為 True）

    Args:
        pid: 欲檢查的 PID。

    Returns:
        bool: True 表示存活，False 表示已結束。
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
