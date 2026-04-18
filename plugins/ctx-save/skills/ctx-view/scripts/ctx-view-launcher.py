#!/usr/bin/env python3
"""ctx-view 啟動層（Launcher）v2.1

職責：
    1. 定位中央集中式 DB（`~/.ctx-save/context.db`）
    2. DB 不存在時透過 importlib 動態載入 ctx-db.py 呼叫 init_db()
    3. 清理 stale PID file（程序已死或身份不符）
    4. 檢查既有 viewer 實例（PID file + `/api/ping` + DB 路徑一致性）可複用則退出
    5. 探測可用 port（29898~29907，支援 lsof 診斷）
    6. 以 subprocess 背景啟動 ctx-viewer.py（detached）
    7. 輪詢 `/api/ping` 驗證啟動成功（最多 3 秒）
    8. 寫入 PID file
    9. 自動開瀏覽器（除非 CTX_VIEW_NO_OPEN=1）

環境變數：
    CTX_VIEW_DB        — 顯式 DB 路徑覆寫（launcher 專用，最高優先級）
    CTX_SAVE_DB_PATH   — 跨工具共用的 DB 路徑覆寫
    CTX_VIEW_PORT      — 起始 port，預設 29898
    CTX_VIEW_NO_OPEN   — 若為 "1" 則不自動開瀏覽器

只使用 Python 標準庫。相容 Python 3.8+。
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from types import ModuleType
from typing import Optional, Tuple

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

# 兄弟 skill（ctx-save）下的資料層模組
CTX_DB_SCRIPT: Path = _HERE.parent.parent / "ctx-save" / "scripts" / "ctx-db.py"

# 中央集中式 DB 目錄與檔案
CTX_SAVE_DIR: Path = Path.home() / ".ctx-save"
CENTRAL_DB: Path = CTX_SAVE_DIR / "context.db"

DEFAULT_START_PORT = 29898
MAX_PORT_TRIES = 10

SERVICE_NAME = "ctx-viewer"
LAUNCHER_VERSION = "v2.1"

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


def _homeify(path: Path) -> str:
    """將絕對路徑中的 $HOME 換成 ~ 以便顯示（跨平台）。

    Args:
        path: 任意絕對路徑。

    Returns:
        str: home-relative 美化後的字串；若不是 home 底下則原樣回傳。
    """
    try:
        return str(path).replace(str(Path.home()), "~", 1)
    except Exception:
        return str(path)


# =============================================================
# ctx-db.py 動態載入（importlib，檔名含 dash 必經之途）
# =============================================================

def _load_ctx_db() -> Optional[ModuleType]:
    """以 importlib 動態載入兄弟 skill 下的 ctx-db.py。

    因檔名為 `ctx-db.py`（dash），無法用 `import ctx_db`，必須走
    `importlib.util.spec_from_file_location` 機制。

    Returns:
        ModuleType | None:
            - 成功 → 載入後的模組物件
            - 檔案不存在或載入失敗 → None（上層負責 fallback）
    """
    if not CTX_DB_SCRIPT.is_file():
        _warn(f"⚠ 找不到 ctx-db.py：{CTX_DB_SCRIPT}（launcher 將走降級路徑）")
        return None

    try:
        spec = importlib.util.spec_from_file_location("ctx_db", CTX_DB_SCRIPT)
        if spec is None or spec.loader is None:
            _warn(f"⚠ 無法建立 ctx-db 模組 spec：{CTX_DB_SCRIPT}")
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:  # pragma: no cover — importlib 錯誤百百種，統一吞掉降級
        _warn(f"⚠ 載入 ctx-db.py 失敗：{exc}（launcher 將走降級路徑）")
        return None


def _fallback_create_empty_db(db_path: Path) -> None:
    """ctx-db.py 無法載入時的降級方案：手動建立最小 schema 的空 DB。

    只建立 `context_saves` 基礎表，實際 schema 與索引由 viewer 首次啟動時
    透過 `ctx_db._run_all_migrations` 補上（冪等）。

    Args:
        db_path: 欲建立的 DB 絕對路徑。

    Raises:
        OSError / sqlite3.Error: 目錄建立或 SQLite 連線失敗時上拋。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _warn(f"⚠ 使用 fallback 建立最小 schema：{_homeify(db_path)}")

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS context_saves (id INTEGER PRIMARY KEY)"
        )
        conn.commit()
    finally:
        conn.close()


# =============================================================
# DB 定位
# =============================================================

def _resolve_db_override() -> Optional[Path]:
    """從環境變數解析 DB 路徑覆寫。

    優先順序：
        1. `CTX_VIEW_DB`       — launcher 專屬顯式覆寫
        2. `CTX_SAVE_DB_PATH`  — 跨工具共用覆寫

    Returns:
        Path | None: 覆寫路徑（未必存在）；兩者皆空則 None。
    """
    for var in ("CTX_VIEW_DB", "CTX_SAVE_DB_PATH"):
        raw = os.environ.get(var, "").strip()
        if raw:
            return Path(raw).expanduser()
    return None


def find_db() -> Path:
    """定位集中式 DB 路徑，必要時建立空 DB。

    決策：
        1. 解析環境變數覆寫（`CTX_VIEW_DB` > `CTX_SAVE_DB_PATH`）
        2. 若無覆寫 → 使用 `~/.ctx-save/context.db`
        3. DB 檔不存在時：
           a. 優先 importlib 載入 ctx-db.py 呼叫 `init_db()`
           b. 載入失敗則手動建立最小 schema 的空 DB（fallback）
        4. 回傳 resolved 絕對路徑

    設計要點：
        - launcher 先確保 DB 存在，再 spawn viewer，避免 viewer 啟動時
          併發 `CREATE TABLE` 造成 `database is locked`（arch.md §10.5）。
        - `init_db` 本身冪等，即使 fallback 建完最小 schema 後 viewer
          再次呼叫也無副作用。

    Returns:
        Path: DB 的絕對路徑（保證 parent 目錄存在；DB 本身為建立後的檔案）。

    Raises:
        SystemExit: 無法建立 DB（覆寫路徑不可寫、disk full 等）時，呼叫端
            以 exit code 1 終止。
    """
    override = _resolve_db_override()
    db_path = (override if override is not None else CENTRAL_DB).resolve()

    if db_path.is_file():
        # DB 已存在：仍跑 schema migration（冪等），確保 v1 舊 DB 升級到最新 schema
        ctx_db = _load_ctx_db()
        if ctx_db is not None:
            run_migrations = getattr(ctx_db, "_run_all_migrations", None)
            connect_db = getattr(ctx_db, "_connect_db", None)
            if callable(run_migrations) and callable(connect_db):
                try:
                    with connect_db(db_path, writer=True) as conn:
                        run_migrations(conn)
                        conn.execute("COMMIT")
                except Exception as exc:
                    _warn(f"⚠ schema migration 失敗（{_homeify(db_path)}）：{exc}")
                    raise SystemExit(1)
        return db_path

    # DB 不存在：優先呼叫 ctx_db.init_db()，失敗則走 fallback
    ctx_db = _load_ctx_db()
    if ctx_db is not None:
        try:
            # ctx-db.py 的 init_db() 無參數，使用 CTX_SAVE_DB_PATH 或預設路徑
            # 若 launcher 端有 CTX_VIEW_DB 覆寫，先同步到 CTX_SAVE_DB_PATH 以便 ctx_db 讀取
            if override is not None and not os.environ.get("CTX_SAVE_DB_PATH"):
                os.environ["CTX_SAVE_DB_PATH"] = str(db_path)
            ctx_db.init_db()
            # 若 ctx_db 有 get_db_path 則以其回傳為準
            getter = getattr(ctx_db, "get_db_path", None)
            if callable(getter):
                try:
                    resolved = Path(getter()).resolve()
                    if resolved.is_file():
                        return resolved
                except Exception:
                    pass
            if db_path.is_file():
                return db_path
        except Exception as exc:
            _warn(f"⚠ ctx_db.init_db() 失敗：{exc}，改走 fallback")

    # Fallback：手動建最小 schema
    try:
        _fallback_create_empty_db(db_path)
    except (OSError, sqlite3.Error) as exc:
        _warn(f"❌ 無法建立 DB（{_homeify(db_path)}）：{exc}")
        raise SystemExit(1)

    return db_path


# =============================================================
# Ping 與 PID 生命週期
# =============================================================

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
    except urllib.error.HTTPError as exc:
        # 503（DB busy）仍視為「服務已啟動」— 解析 body 回傳，交由呼叫方判斷
        if exc.code == 503:
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _cleanup_stale_pid() -> None:
    """嚴格版 stale PID 清理。

    規則：
        - pidfile 不存在 → 直接 return（無事可做）
        - 程序不存活 → `clear_pidfile`
        - 程序存活但 ping 失敗或 service 名稱不符 → `clear_pidfile`
          （**不殺 process**，可能是別的應用剛好搶到同 PID）

    此函式是 `try_reuse_existing` 的前置保險；呼叫後 pidfile 若仍存在，
    代表 viewer 實例健康可進一步驗證。
    """
    data = pidfile.read_pidfile()
    if data is None:
        return

    pid = data.get("pid")
    port = data.get("port")
    if not isinstance(pid, int) or not isinstance(port, int):
        pidfile.clear_pidfile()
        return

    if not pidfile.is_alive(pid):
        pidfile.clear_pidfile()
        return

    body = _ping(port)
    if body is None or body.get("service") != SERVICE_NAME:
        # 程序存活但身份不符 — 清 pidfile，不殺 process
        pidfile.clear_pidfile()
        return


def try_reuse_existing(expected_db: Path) -> Optional[Tuple[int, int]]:
    """檢查是否存在可複用的既有 viewer。

    流程：
        1. `_cleanup_stale_pid()` 清掉明顯失效的記錄
        2. 重讀 pidfile；不存在 → None
        3. 再次驗證 is_alive / ping service
        4. **新增**：比對 pidfile 中 `db` 欄位與 `expected_db` 是否一致
           不一致 → 視為 stale → `clear_pidfile` → None（強制重啟新 DB）
        5. 全部通過 → 回 (pid, port) 可複用

    Args:
        expected_db: 當前 `find_db()` 回傳的 DB 絕對路徑；用於一致性比對。

    Returns:
        tuple(pid, port) | None: 可複用時回 (pid, port)；否則 None。
    """
    _cleanup_stale_pid()

    data = pidfile.read_pidfile()
    if data is None:
        return None

    pid = data["pid"]
    port = data["port"]

    if not pidfile.is_alive(pid):
        pidfile.clear_pidfile()
        return None

    body = _ping(port)
    if body is None or body.get("service") != SERVICE_NAME:
        pidfile.clear_pidfile()
        return None

    # DB 路徑一致性比對（v2.1 新增）
    recorded_db = data.get("db")
    try:
        recorded_resolved = Path(recorded_db).resolve() if isinstance(recorded_db, str) else None
    except Exception:
        recorded_resolved = None

    if recorded_resolved != expected_db:
        _warn(
            f"⚠ 既有 viewer 使用的 DB ({_homeify(recorded_resolved) if recorded_resolved else recorded_db}) "
            f"與目前目標 ({_homeify(expected_db)}) 不一致，將重啟"
        )
        pidfile.clear_pidfile()
        return None

    return (pid, port)


# =============================================================
# 啟動 / 清理子程序
# =============================================================

def spawn_viewer(port: int, db: str, log_path: Path) -> int:
    """以 subprocess 背景啟動 ctx-viewer.py。

    特性：
        - stdout/stderr 重導至 log_path（覆寫模式，保留最近一次啟動）
        - stdin 設為 /dev/null
        - start_new_session=True（脫離 terminal session）

    Args:
        port: 傳給 viewer 的 --port 參數
        db: 傳給 viewer 的 --db 參數（absolute path string）
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


# =============================================================
# 畫面輸出
# =============================================================

def _print_banner(db: Path, port: int, pid: int, version: Optional[str] = None) -> None:
    """列印首次啟動成功的橫幅訊息。

    Args:
        db: DB 絕對路徑（會 home-relative 美化）。
        port: viewer 監聽 port。
        pid: viewer 背景程序 PID。
        version: `/api/ping` 回傳的版本字串；None 則使用 `LAUNCHER_VERSION`。
    """
    url = f"http://localhost:{port}"
    display_db = _homeify(db)
    ver = version or LAUNCHER_VERSION
    _log(f"⚙ Context Save Viewer ({ver})")
    _log(SEPARATOR)
    _log(f"📂 DB:   {display_db}")
    _log(f"🌐 URL:  {url}")
    _log(f"PID:     {pid} (背景執行)")
    _log(SEPARATOR)
    _log("使用 /ctx-view-stop 停止")


def _print_reuse(port: int, pid: int, db: Optional[Path] = None) -> None:
    """列印複用既有實例的訊息。"""
    _log("♻ 重用既有 viewer")
    _log(f"🌐 URL:  http://localhost:{port}")
    _log(f"PID:     {pid}")
    if db is not None:
        _log(f"📂 DB:   {_homeify(db)}")


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
    # ---- step 1. 定位/建立中央 DB ----
    try:
        db_path = find_db()
    except SystemExit as exc:
        # find_db 內部已印錯誤訊息
        return int(exc.code) if isinstance(exc.code, int) else 1

    db_str = str(db_path)

    # ---- step 2. 檢查既有實例可否複用（含 stale 清理 + DB 一致性） ----
    reused = try_reuse_existing(expected_db=db_path)
    if reused is not None:
        pid, port = reused
        _print_reuse(port, pid, db=db_path)
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

    # 取得 viewer 回報的版本（用於 banner 顯示）
    ping_body = _ping(port)
    viewer_version = None
    if isinstance(ping_body, dict):
        v = ping_body.get("version")
        if isinstance(v, str) and v:
            viewer_version = v if v.startswith("v") else f"v{v}"

    # ---- step 6. 寫 PID file ----
    try:
        pidfile.write_pidfile(pid=child_pid, port=port, db=db_str)
    except OSError as exc:
        # PID file 寫入失敗不致命（viewer 仍可用，只是下次無法複用）
        _warn(f"⚠ PID file 寫入失敗：{exc}（viewer 仍已啟動）")

    # ---- step 7. 印 banner + 開瀏覽器 ----
    _print_banner(db=db_path, port=port, pid=child_pid, version=viewer_version)
    _open_browser_if_allowed(port)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        _warn("\n⚠ 已取消")
        sys.exit(130)
