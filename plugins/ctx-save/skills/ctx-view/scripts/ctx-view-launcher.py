#!/usr/bin/env python3
"""ctx-view 啟動層（Launcher）

職責：
    1. 定位 `.ctx-save/context.db`（從 cwd 往上遞迴）
    2. 檢查既有 viewer 實例（PID file + `/api/ping`）可複用則退出
    3. 探測可用 port（29898~29907，支援 lsof 診斷）
    4. 以 subprocess 背景啟動 ctx-viewer.py（detached）
    5. 輪詢 `/api/ping` 驗證啟動成功（最多 3 秒）
    6. 寫入 PID file
    7. 自動開瀏覽器（除非 CTX_VIEW_NO_OPEN=1）

環境變數：
    CTX_VIEW_PORT      — 起始 port，預設 29898
    CTX_VIEW_NO_OPEN   — 若為 "1" 則不自動開瀏覽器

只使用 Python 標準庫。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional

# 將 lib 目錄加入 sys.path 後 import 共用模組
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lib import pidfile  # noqa: E402
from lib import port_probe  # noqa: E402


# =============================================================
# 常數
# =============================================================

# 兄弟 skill（ctx-save）下的 viewer 腳本
VIEWER_SCRIPT: Path = _HERE.parent.parent / "ctx-save" / "scripts" / "ctx-viewer.py"

DB_RELATIVE = Path(".ctx-save") / "context.db"

DEFAULT_START_PORT = 29898
MAX_PORT_TRIES = 10

SERVICE_NAME = "ctx-viewer"
PING_TIMEOUT_SEC = 0.5           # 單次 ping 的連線逾時
SPAWN_WAIT_TIMEOUT_SEC = 3.0     # 子程序啟動後等待 ping 成功的最大時間
SPAWN_POLL_INTERVAL_SEC = 0.1

SEPARATOR = "━" * 30


# =============================================================
# 工具函式
# =============================================================

def _log(msg: str) -> None:
    """統一輸出到 stdout（使用者可見）。"""
    print(msg, flush=True)


def _warn(msg: str) -> None:
    """輸出警告到 stderr。"""
    print(msg, file=sys.stderr, flush=True)


def _cache_dir() -> Path:
    """取得 ctx-viewer 的 cache 目錄（與 pidfile 同根）。"""
    return pidfile.pidfile_path().parent


def find_db() -> Optional[Path]:
    """從 cwd 往上遞迴尋找 `.ctx-save/context.db`。

    Returns:
        Path | None: 找到的 DB 絕對路徑；找不到則 None。
    """
    current = Path.cwd().resolve()
    # 包含當前目錄與所有父層
    candidates = [current] + list(current.parents)
    for directory in candidates:
        candidate = directory / DB_RELATIVE
        if candidate.is_file():
            return candidate.resolve()
    return None


def _ping(port: int, timeout: float = PING_TIMEOUT_SEC) -> Optional[dict]:
    """GET http://127.0.0.1:{port}/api/ping。

    Returns:
        dict | None:
            - 成功且 JSON 解析成功 → 回傳 body dict
            - 任何錯誤（連線失敗、逾時、非 JSON）→ None
    """
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


def try_reuse_existing() -> Optional[tuple]:
    """檢查是否存在可複用的既有 viewer。

    流程：
        1. 讀 PID file
        2. is_alive(pid) 檢查
        3. GET /api/ping 驗證服務身份（service == "ctx-viewer"）

    若任一步失敗且 PID file 存在，清除 stale 檔案。

    Returns:
        tuple(pid, port) | None: 可複用時回傳 (pid, port)；否則 None。
    """
    data = pidfile.read_pidfile()
    if data is None:
        return None

    pid = data["pid"]
    port = data["port"]

    if not pidfile.is_alive(pid):
        pidfile.clear_pidfile()
        return None

    ping_body = _ping(port)
    if ping_body is None or ping_body.get("service") != SERVICE_NAME:
        # 程序存活但不是本服務 — 視為 stale，清除後重啟
        pidfile.clear_pidfile()
        return None

    return (pid, port)


def spawn_viewer(port: int, db: str, log_path: Path) -> int:
    """以 subprocess 背景啟動 ctx-viewer.py。

    特性：
        - stdout/stderr 重導至 log_path（覆寫模式，保留最近一次啟動）
        - stdin 設為 /dev/null
        - start_new_session=True（脫離 terminal session）

    Args:
        port: 傳給 viewer 的 --port 參數
        db: 傳給 viewer 的 --db 參數
        log_path: 子程序 stdout/stderr 的輸出檔

    Returns:
        int: 背景子程序的 PID

    Raises:
        FileNotFoundError: VIEWER_SCRIPT 不存在
        OSError: 建立 log 檔案失敗
    """
    if not VIEWER_SCRIPT.is_file():
        raise FileNotFoundError(f"找不到 viewer 腳本：{VIEWER_SCRIPT}")

    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 使用 'w' 覆寫模式（spec §5.1：滾動保留最近一次啟動）
    log_file = open(log_path, "w", encoding="utf-8")

    cmd = [
        sys.executable,
        str(VIEWER_SCRIPT),
        "--port", str(port),
        "--db", db,
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
    return proc.pid


def wait_for_ping(port: int, timeout: float = SPAWN_WAIT_TIMEOUT_SEC) -> bool:
    """輪詢 ``/api/ping`` 直到成功或逾時。

    Args:
        port: 欲檢查的 port
        timeout: 最長等待秒數

    Returns:
        bool: True 表示 ping 成功（且 service 欄位正確）。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _ping(port)
        if body is not None and body.get("service") == SERVICE_NAME:
            return True
        time.sleep(SPAWN_POLL_INTERVAL_SEC)
    return False


def _kill_child(pid: int) -> None:
    """盡力終止子程序（啟動失敗時清理用）。"""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    # 稍待 0.5 秒，若仍存活則 SIGKILL
    for _ in range(5):
        if not pidfile.is_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _print_banner(db: str, port: int, pid: int) -> None:
    """列印首次啟動成功的橫幅訊息。"""
    url = f"http://localhost:{port}"
    _log("⚙ Context Save Viewer")
    _log(SEPARATOR)
    _log(f"📂 DB:   {db}")
    _log(f"🌐 URL:  {url}")
    _log(f"PID:     {pid} (背景執行)")
    _log(SEPARATOR)
    _log("使用 /ctx-view-stop 停止")


def _print_reuse(port: int, pid: int) -> None:
    """列印複用既有實例的訊息。"""
    _log("♻ 重用既有 viewer")
    _log(f"🌐 URL:  http://localhost:{port}")
    _log(f"PID:     {pid}")


def _open_browser_if_allowed(port: int) -> None:
    """若 CTX_VIEW_NO_OPEN != "1" 則開啟瀏覽器。"""
    if os.environ.get("CTX_VIEW_NO_OPEN") == "1":
        return
    url = f"http://localhost:{port}"
    try:
        webbrowser.open(url)
    except Exception:
        # 瀏覽器無法開啟不是致命錯誤
        _warn(f"⚠ 無法自動開啟瀏覽器，請手動前往：{url}")


# =============================================================
# 主流程
# =============================================================

def _resolve_start_port() -> int:
    """從環境變數解析起始 port，失敗則退回預設值。"""
    raw = os.environ.get("CTX_VIEW_PORT", "").strip()
    if not raw:
        return DEFAULT_START_PORT
    try:
        value = int(raw)
        if 1 <= value <= 65535:
            return value
    except ValueError:
        pass
    _warn(
        f"⚠ CTX_VIEW_PORT={raw!r} 不是合法 port，改用預設 {DEFAULT_START_PORT}"
    )
    return DEFAULT_START_PORT


def _on_port_conflict(port: int, occupant: Optional[dict]) -> None:
    """port_probe 衝突回呼 — 印使用者可見訊息。"""
    next_port = port + 1
    if occupant is not None:
        pid = occupant.get("pid")
        comm = occupant.get("command")
        _log(f"⚠ Port {port} 被 PID {pid} ({comm}) 占用，改用 {next_port}")
    else:
        _log(f"⚠ Port {port} 被占用（無法取得占用程序資訊），改用 {next_port}")


def main() -> int:
    """進入點。回傳 process exit code。"""
    # ---- step 1. 定位 DB ----
    db_path = find_db()
    if db_path is None:
        _warn("❌ 找不到 context.db，請先用 /ctx-save 儲存至少一筆")
        return 1

    db_str = str(db_path)

    # ---- step 2. 檢查既有實例可否複用 ----
    reused = try_reuse_existing()
    if reused is not None:
        pid, port = reused
        _print_reuse(port, pid)
        _open_browser_if_allowed(port)
        return 0

    # ---- step 3. 探測可用 port ----
    start_port = _resolve_start_port()
    try:
        port = port_probe.find_available_port(
            start=start_port,
            max_tries=MAX_PORT_TRIES,
            on_conflict=_on_port_conflict,
        )
    except RuntimeError as exc:
        last = start_port + MAX_PORT_TRIES - 1
        _warn(
            "❌ 無可用 port，請釋放 "
            f"{start_port}-{last} 範圍或設定 CTX_VIEW_PORT"
        )
        _warn(f"   詳細：{exc}")
        return 2

    # ---- step 4. 背景啟動 viewer ----
    log_path = _cache_dir() / "viewer.log"
    try:
        child_pid = spawn_viewer(port=port, db=db_str, log_path=log_path)
    except FileNotFoundError as exc:
        _warn(f"❌ {exc}")
        return 3
    except OSError as exc:
        _warn(f"❌ 啟動失敗：{exc}")
        return 3

    # ---- step 5. 等待 ping 驗證 ----
    if not wait_for_ping(port):
        _kill_child(child_pid)
        _warn(
            "❌ Server 啟動逾時，請檢查 "
            f"{log_path}"
        )
        return 4

    # ---- step 6. 寫 PID file ----
    try:
        pidfile.write_pidfile(pid=child_pid, port=port, db=db_str)
    except OSError as exc:
        # PID file 寫入失敗不致命（viewer 仍可用，只是下次無法複用）
        _warn(f"⚠ PID file 寫入失敗：{exc}（viewer 仍已啟動）")

    # ---- step 7. 印 banner + 開瀏覽器 ----
    _print_banner(db=db_str, port=port, pid=child_pid)
    _open_browser_if_allowed(port)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _warn("\n⚠ 已取消")
        sys.exit(130)
