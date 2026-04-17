#!/usr/bin/env python3
"""ctx-view-stop：停止背景 ctx-viewer 服務。

流程（對應 arch.md §4）：
    1. 讀 PID file；不存在 → ℹ Viewer 未執行中（退 0）
    2. is_alive(pid) 檢查；已死 → 清 PID file 並提示（退 0）
    3. GET /api/ping 二次驗證 service=="ctx-viewer"；
       無回應或不符 → ⚠ 拒絕停止（退非 0）
    4. SIGTERM → 最多等 2 秒 → 仍存活則 SIGKILL
    5. 清 PID file，印「✅ Viewer 已停止」

僅使用 Python 標準庫。
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# =============================================================
# 引入共用 lib（來自 ctx-view skill）
# =============================================================

_HERE = Path(__file__).resolve().parent
# 兄弟 skill 的 scripts 目錄
_CTX_VIEW_SCRIPTS = _HERE.parent.parent / "ctx-view" / "scripts"
if str(_CTX_VIEW_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_CTX_VIEW_SCRIPTS))

from lib import pidfile  # noqa: E402


# =============================================================
# 常數
# =============================================================

SERVICE_NAME = "ctx-viewer"
PING_TIMEOUT_SEC = 0.5
SIGTERM_WAIT_SEC = 2.0
POLL_INTERVAL_SEC = 0.1


# =============================================================
# 工具函式
# =============================================================

def _log(msg: str) -> None:
    print(msg, flush=True)


def _warn(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _ping(port: int, timeout: float = PING_TIMEOUT_SEC) -> Optional[dict]:
    """GET /api/ping。失敗則回 None。"""
    url = f"http://127.0.0.1:{port}/api/ping"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _send_signal(pid: int, sig: int) -> bool:
    """發送訊號至 PID。成功或目標已不存在視為可接受。

    Returns:
        bool: True 表示操作成功或目標已消失；False 表示權限拒絕或其他 OSError。
    """
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    except OSError:
        return False
    return True


def _wait_for_exit(pid: int, timeout: float) -> bool:
    """輪詢直到 pid 消失或逾時。

    Returns:
        bool: True 表示已結束；False 表示逾時仍存活。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pidfile.is_alive(pid):
            return True
        time.sleep(POLL_INTERVAL_SEC)
    return not pidfile.is_alive(pid)


# =============================================================
# 主流程
# =============================================================

def main() -> int:
    """進入點。"""
    # ---- step 1. 讀 PID file ----
    data = pidfile.read_pidfile()
    if data is None:
        _log("ℹ Viewer 未執行中")
        return 0

    pid = data["pid"]
    port = data["port"]

    # ---- step 2. 檢查程序存活 ----
    if not pidfile.is_alive(pid):
        pidfile.clear_pidfile()
        _log(f"ℹ 偵測 stale PID（{pid} 已不存在），已清理 PID file")
        return 0

    # ---- step 3. 二次驗證：GET /api/ping ----
    ping_body = _ping(port)
    if ping_body is None or ping_body.get("service") != SERVICE_NAME:
        _warn(f"⚠ PID {pid} 非本服務，拒絕停止")
        _warn(f"   （port {port} 的 /api/ping 未回應或服務名稱不符）")
        return 2

    # ---- step 4. SIGTERM → 等待 → SIGKILL ----
    if not _send_signal(pid, signal.SIGTERM):
        _warn(f"❌ 無法對 PID {pid} 發送 SIGTERM（權限不足或系統錯誤）")
        return 3

    exited = _wait_for_exit(pid, SIGTERM_WAIT_SEC)
    if not exited:
        # SIGTERM 未成功 → 強制 SIGKILL
        if not _send_signal(pid, signal.SIGKILL):
            _warn(f"❌ 無法對 PID {pid} 發送 SIGKILL")
            return 3
        # 最後再等一小段時間確認
        _wait_for_exit(pid, 1.0)

    # ---- step 5. 清 PID file + 輸出訊息 ----
    pidfile.clear_pidfile()
    _log("✅ Viewer 已停止")
    _log(f"   PID:   {pid}")
    _log(f"   Port:  {port}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _warn("\n⚠ 已取消")
        sys.exit(130)
