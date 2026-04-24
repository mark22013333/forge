#!/usr/bin/env python3
"""ctx-db.py — Context Save SQLite 操作腳本（v2.1 集中式 DB）

用法:
    ctx-db.py init                         初始化資料庫
    ctx-db.py migrate                      執行資料庫遷移（冪等）
    ctx-db.py migrate-legacy [--dry-run]   掃描並合併舊 per-project DB
    ctx-db.py save                         儲存記錄（從 stdin 讀取 JSON）
    ctx-db.py list [limit]                 列出快照（預設 20；最大 500）
    ctx-db.py list-projects                列出所有專案（含筆數）
    ctx-db.py get <session_id>             取得特定 session 的所有記錄
    ctx-db.py search <keyword>             搜尋關鍵字（keyword 長度 ≤ 200）
    ctx-db.py clean [days]                 清除 N 天前的記錄（預設 30）
    ctx-db.py stats                        統計資訊（含 by_project）

環境變數:
    CTX_SAVE_DB_PATH     覆寫中央 DB 路徑（預設 ~/.ctx-save/context.db）
    CTX_SAVE_NO_MIGRATE  設定則跳過 auto-migrate
    CTX_PROJECT          寫入時的 project_path 覆寫（預設 Path.cwd()）
"""

import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

DB_NAME = "context.db"
DEFAULT_CENTRAL_DIR = Path.home() / ".ctx-save"
DEFAULT_DB_PATH = DEFAULT_CENTRAL_DIR / DB_NAME
# v2 舊 flag 保留供降版偵測用；v3 啟用新 flag 以觸發 v3 migration
OLD_FLAG = DEFAULT_CENTRAL_DIR / ".migration-done"
MIGRATION_FLAG = DEFAULT_CENTRAL_DIR / ".migration-v3-done"

# 白名單正則（新寫入才套用）
CATEGORY_REGEX = re.compile(r"^[a-z0-9_-]{1,32}$")
SESSION_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 長度上限
MAX_SEARCH_LEN = 200
MAX_LIST_LIMIT = 500

# v3：clean 時對剛還原過的 snapshot 額外保護的天數
RESTORE_PROTECT_DAYS = 7

# ---------------------------------------------------------------------------
# 路徑策略
# ---------------------------------------------------------------------------


def get_db_path() -> Path:
    """取得中央 DB 路徑。

    優先序：
      1. 環境變數 CTX_SAVE_DB_PATH
      2. ~/.ctx-save/context.db
    """
    override = os.environ.get("CTX_SAVE_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_DB_PATH


def _resolve_project_path() -> str:
    """決定寫入時的 project_path（POSIX 字串）。

    優先序：
      1. 環境變數 CTX_PROJECT
      2. Path.cwd().resolve()
    """
    value = os.environ.get("CTX_PROJECT")
    if value:
        path = Path(value).expanduser().resolve()
    else:
        path = Path.cwd().resolve()
    # 統一轉 POSIX 字串（Windows 會變成 /C:/Users/... 但跨平台一致）
    return str(PurePosixPath(path.as_posix()))


# ---------------------------------------------------------------------------
# 連線工廠
# ---------------------------------------------------------------------------


def _apply_secure_permissions(db_path: Path) -> None:
    """將 DB / WAL / SHM 檔鎖為 0o600、父目錄鎖為 0o700。

    多使用者主機（shared dev box / CI runner）避免旁人讀取 snapshot
    內容（可能含未清洗的 API key / 密碼）。Windows chmod 語義不同，
    失敗一律靜默。
    """
    try:
        os.chmod(db_path.parent, 0o700)
    except OSError:
        pass
    try:
        if db_path.exists():
            os.chmod(db_path, 0o600)
    except OSError:
        pass
    # SQLite WAL / SHM 檔名慣例為在原檔名後加 `-wal` / `-shm`
    for suffix in ("-wal", "-shm"):
        sibling = db_path.parent / (db_path.name + suffix)
        try:
            if sibling.exists():
                os.chmod(sibling, 0o600)
        except OSError:
            pass


def _configure_connection(conn: sqlite3.Connection, writer: bool = False) -> None:
    """對連線套用統一 PRAGMA。writer=True 時再 BEGIN IMMEDIATE。"""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    if writer:
        # 立即取得 write lock，避免 reader → writer 升級時才發現衝突
        conn.execute("BEGIN IMMEDIATE")


def _connect_db(db_path: Path, writer: bool = False) -> sqlite3.Connection:
    """建立連線並套 PRAGMA。

    寫入端必 writer=True（自動 BEGIN IMMEDIATE）；
    讀取端留空。回傳的 Connection 可用 `with ... as conn:` 自動 commit/close。
    """
    path_obj = Path(db_path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None 讓 PRAGMA / BEGIN IMMEDIATE 不受 Python DB-API 自動交易干擾
    conn = sqlite3.connect(str(path_obj), isolation_level=None)
    _configure_connection(conn, writer=writer)
    # 每次連線後都 chmod 一次；WAL/SHM 在 PRAGMA journal_mode=WAL 後才生成，
    # 因此這時機最保險。idempotent + 失敗靜默，開銷可忽略。
    _apply_secure_permissions(path_obj)
    return conn


# ---------------------------------------------------------------------------
# Schema 初始化與 Migration
# ---------------------------------------------------------------------------


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """以 PRAGMA table_info 偵測欄位是否存在（冪等 migration 用）。"""
    cursor = conn.execute("PRAGMA table_info(" + table + ")")
    return any(row[1] == column for row in cursor.fetchall())


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    """以 sqlite_master 偵測表是否存在（冪等 migration 用）。"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cursor.fetchone() is not None


def _init_schema(conn: sqlite3.Connection) -> None:
    """建立 schema 與索引（冪等）。

    對新 DB：直接建立 v3 全欄位；舊 DB 由 migrate_* 漸進升級。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project_path TEXT NOT NULL DEFAULT '(unknown)',
            context_percentage REAL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            model TEXT DEFAULT '',
            deleted_at DATETIME,
            is_persistent INTEGER NOT NULL DEFAULT 0,
            restored_at DATETIME,
            distilled_from_id INTEGER
        )
        """
    )
    # v3 診斷結果
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_diagnoses (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id           TEXT NOT NULL,
            project_path         TEXT NOT NULL DEFAULT '(unknown)',
            ran_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
            context_percentage   REAL,
            score                INTEGER,
            tokens_conversation  INTEGER,
            tokens_files         INTEGER,
            tokens_tools_schema  INTEGER,
            tokens_tools_runtime INTEGER,
            tokens_system        INTEGER,
            findings_json        TEXT NOT NULL,
            suggest_focus        TEXT,
            deleted_at           DATETIME
        )
        """
    )
    # v3 compact 事件時間軸
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_compact_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            project_path TEXT NOT NULL DEFAULT '(unknown)',
            happened_at  DATETIME NOT NULL,
            trigger      TEXT,
            context_pct  REAL,
            snapshot_id  INTEGER,
            reinjected   INTEGER NOT NULL DEFAULT 0,
            deleted_at   DATETIME
        )
        """
    )
    # 基本索引 — 只依賴 v2.3 既有欄位，對舊 DB / 新 DB 皆可安全執行
    base_indexes = [
        "CREATE INDEX IF NOT EXISTS idx_saves_session  ON context_saves(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_saves_created  ON context_saves(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_saves_category ON context_saves(category)",
        # context_diagnoses / context_compact_events 的索引（新表在上方已建立）
        "CREATE INDEX IF NOT EXISTS idx_diag_session "
        "ON context_diagnoses(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_diag_ran "
        "ON context_diagnoses(ran_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_diag_project_ran "
        "ON context_diagnoses(project_path, ran_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_diag_deleted "
        "ON context_diagnoses(deleted_at)",
        "CREATE INDEX IF NOT EXISTS idx_compact_happened "
        "ON context_compact_events(happened_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_compact_session "
        "ON context_compact_events(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_compact_reinjected "
        "ON context_compact_events(reinjected)",
    ]
    for sql in base_indexes:
        conn.execute(sql)

    # 依賴 v2 / v3 新欄位的索引：只在對應欄位已存在時才建
    # （舊 DB 初次跑 _init_schema 時會缺這些欄位，交由 migrate_* 補欄位時同步建索引）
    if _has_column(conn, "context_saves", "deleted_at"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saves_deleted "
            "ON context_saves(deleted_at)"
        )
    if _has_column(conn, "context_saves", "project_path"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saves_project "
            "ON context_saves(project_path)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saves_project_created "
            "ON context_saves(project_path, created_at DESC)"
        )
    if _has_column(conn, "context_saves", "is_persistent"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saves_persistent "
            "ON context_saves(is_persistent)"
        )
    if _has_column(conn, "context_saves", "restored_at"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saves_restored "
            "ON context_saves(restored_at DESC)"
        )
    if _has_column(conn, "context_saves", "distilled_from_id"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_saves_distilled "
            "ON context_saves(distilled_from_id)"
        )


def migrate_add_deleted_at(conn: sqlite3.Connection) -> bool:
    """冪等遷移：偵測 deleted_at 欄位是否存在，不存在才加。

    Returns:
        True 表示實際執行了遷移，False 表示已是最新版。
    """
    cursor = conn.execute("PRAGMA table_info(context_saves)")
    columns = {row[1] for row in cursor.fetchall()}
    if "deleted_at" in columns:
        return False
    conn.execute("ALTER TABLE context_saves ADD COLUMN deleted_at DATETIME")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_saves_deleted "
        "ON context_saves(deleted_at)"
    )
    return True


def migrate_add_project_path(conn: sqlite3.Connection) -> bool:
    """冪等遷移：為舊 DB 補上 project_path 欄位並回填 '(unknown)'。

    SQLite 不支援 NOT NULL 無預設的 ALTER ADD，因此先加 nullable 再回填。
    新 DB 由 _init_schema 直接 NOT NULL DEFAULT，不會走到這裡。

    Returns:
        True 表示實際加過欄位，False 表示已存在。
    """
    cursor = conn.execute("PRAGMA table_info(context_saves)")
    columns = {row[1] for row in cursor.fetchall()}
    if "project_path" in columns:
        return False
    conn.execute("ALTER TABLE context_saves ADD COLUMN project_path TEXT")
    conn.execute(
        "UPDATE context_saves SET project_path = '(unknown)' "
        "WHERE project_path IS NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_saves_project "
        "ON context_saves(project_path)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_saves_project_created "
        "ON context_saves(project_path, created_at DESC)"
    )
    return True


def migrate_add_persistent(conn: sqlite3.Connection) -> bool:
    """v3 冪等遷移：為 context_saves 補上 is_persistent 欄位。

    Returns:
        True 表示實際執行，False 表示欄位已存在。
    """
    if _has_column(conn, "context_saves", "is_persistent"):
        return False
    conn.execute(
        "ALTER TABLE context_saves "
        "ADD COLUMN is_persistent INTEGER NOT NULL DEFAULT 0"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_saves_persistent "
        "ON context_saves(is_persistent)"
    )
    return True


def migrate_add_restored_at(conn: sqlite3.Connection) -> bool:
    """v3 冪等遷移：為 context_saves 補上 restored_at 欄位。

    Returns:
        True 表示實際執行，False 表示欄位已存在。
    """
    if _has_column(conn, "context_saves", "restored_at"):
        return False
    conn.execute("ALTER TABLE context_saves ADD COLUMN restored_at DATETIME")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_saves_restored "
        "ON context_saves(restored_at DESC)"
    )
    return True


def migrate_add_distilled_from(conn: sqlite3.Connection) -> bool:
    """v3 冪等遷移：為 context_saves 補上 distilled_from_id 欄位。

    Returns:
        True 表示實際執行，False 表示欄位已存在。
    """
    if _has_column(conn, "context_saves", "distilled_from_id"):
        return False
    conn.execute(
        "ALTER TABLE context_saves ADD COLUMN distilled_from_id INTEGER"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_saves_distilled "
        "ON context_saves(distilled_from_id)"
    )
    return True


def migrate_create_diagnoses_table(conn: sqlite3.Connection) -> bool:
    """v3 冪等遷移：建立 context_diagnoses 表與索引。

    Returns:
        True 表示實際建表，False 表示表已存在。
    """
    if _has_table(conn, "context_diagnoses"):
        return False
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_diagnoses (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id           TEXT NOT NULL,
            project_path         TEXT NOT NULL DEFAULT '(unknown)',
            ran_at               DATETIME DEFAULT CURRENT_TIMESTAMP,
            context_percentage   REAL,
            score                INTEGER,
            tokens_conversation  INTEGER,
            tokens_files         INTEGER,
            tokens_tools_schema  INTEGER,
            tokens_tools_runtime INTEGER,
            tokens_system        INTEGER,
            findings_json        TEXT NOT NULL,
            suggest_focus        TEXT,
            deleted_at           DATETIME
        )
        """
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_diag_session "
        "ON context_diagnoses(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_diag_ran "
        "ON context_diagnoses(ran_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_diag_project_ran "
        "ON context_diagnoses(project_path, ran_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_diag_deleted "
        "ON context_diagnoses(deleted_at)",
    ):
        conn.execute(sql)
    return True


def migrate_create_compact_events_table(conn: sqlite3.Connection) -> bool:
    """v3 冪等遷移：建立 context_compact_events 表與索引。

    Returns:
        True 表示實際建表，False 表示表已存在。
    """
    if _has_table(conn, "context_compact_events"):
        return False
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS context_compact_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id   TEXT NOT NULL,
            project_path TEXT NOT NULL DEFAULT '(unknown)',
            happened_at  DATETIME NOT NULL,
            trigger      TEXT,
            context_pct  REAL,
            snapshot_id  INTEGER,
            reinjected   INTEGER NOT NULL DEFAULT 0,
            deleted_at   DATETIME
        )
        """
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_compact_happened "
        "ON context_compact_events(happened_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_compact_session "
        "ON context_compact_events(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_compact_reinjected "
        "ON context_compact_events(reinjected)",
    ):
        conn.execute(sql)
    return True


def _run_all_migrations(conn: sqlite3.Connection) -> Dict[str, bool]:
    """依序跑所有 migration，回報每一步是否實際執行。"""
    return {
        "deleted_at": migrate_add_deleted_at(conn),
        "project_path": migrate_add_project_path(conn),
        "persistent": migrate_add_persistent(conn),
        "restored_at": migrate_add_restored_at(conn),
        "distilled_from_id": migrate_add_distilled_from(conn),
        "diagnoses_table": migrate_create_diagnoses_table(conn),
        "compact_events_table": migrate_create_compact_events_table(conn),
    }


def init_db(db_path: Optional[Union[str, Path]] = None) -> str:
    """初始化資料庫（冪等）。

    無參數時用 get_db_path()；為利 importlib 呼叫保留可傳入參數。
    """
    target = Path(db_path) if db_path else get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _connect_db(target, writer=True) as conn:
        _init_schema(conn)
        _run_all_migrations(conn)
        conn.execute("COMMIT")
    return str(target)


# ---------------------------------------------------------------------------
# 驗證與白名單
# ---------------------------------------------------------------------------


def _sanitize_category(value: str) -> Tuple[str, Optional[str]]:
    """檢查 category 是否符合白名單。

    Returns:
        (cleaned, original_if_rejected)
        合法 → (原值, None)
        非法 → ("custom", 原值)   — 原值由 caller 決定是否附加到 content
    """
    if isinstance(value, str) and CATEGORY_REGEX.match(value):
        return value, None
    return "custom", value if value else None


def _validate_session_id(value: str) -> bool:
    """驗證 session_id 格式。"""
    return isinstance(value, str) and bool(SESSION_ID_REGEX.match(value))


# ---------------------------------------------------------------------------
# Legacy 合併
# ---------------------------------------------------------------------------


def _legacy_fallback_project_path(legacy_db: Path) -> str:
    """legacy DB 缺 project_path 時的回填值。

    legacy 路徑形如 `{project}/.ctx-save/context.db`，
    回傳 `{project}` 的 POSIX 字串。
    """
    # context.db → .ctx-save → {project}
    project_dir = legacy_db.parent.parent
    return str(PurePosixPath(project_dir.resolve().as_posix()))


def _legacy_scan_roots() -> List[Path]:
    """決定 legacy DB 掃描根目錄清單。

    優先序：
        1. CTX_SAVE_MIGRATE_ROOTS 環境變數（冒號分隔的絕對路徑）
        2. 預設候選：~/IdeaProjects、~/Projects、~/workspace、~/code、~/src
           — 只保留實際存在的目錄

    刻意**不**掃整個 ~，避免 Library / Caches / node_modules 等巨量無關檔案。
    """
    raw = os.environ.get("CTX_SAVE_MIGRATE_ROOTS", "").strip()
    if raw:
        roots = [Path(p).expanduser() for p in raw.split(":") if p.strip()]
        return [r for r in roots if r.is_dir()]
    home = Path.home()
    candidates = [
        home / "IdeaProjects",
        home / "Projects",
        home / "workspace",
        home / "code",
        home / "src",
    ]
    return [c for c in candidates if c.is_dir()]


def _scan_legacy_dbs(central_db: Path) -> List[Path]:
    """掃描 workspace 根目錄下的 legacy DB，排除中央 DB 自身與 DEFAULT_CENTRAL_DIR。"""
    central_resolved = central_db.resolve()
    central_dir_resolved = DEFAULT_CENTRAL_DIR.resolve()
    found: List[Path] = []
    for root in _legacy_scan_roots():
        try:
            for candidate in root.rglob(".ctx-save/context.db"):
                try:
                    resolved = candidate.resolve()
                except OSError:
                    continue
                # 排除中央 DB 本身
                if resolved == central_resolved:
                    continue
                # 排除 ~/.ctx-save/ 目錄底下的任何 context.db
                try:
                    resolved.relative_to(central_dir_resolved)
                    continue
                except ValueError:
                    pass
                found.append(resolved)
        except (PermissionError, OSError):
            # rglob 在無權限目錄上會噴錯，忽略該 root
            continue
    return found


def _legacy_has_column(conn: sqlite3.Connection, alias: str, column: str) -> bool:
    """檢查 attached DB 的 context_saves 是否有特定欄位。"""
    cursor = conn.execute(
        "SELECT name FROM " + alias + ".sqlite_master "
        "WHERE type='table' AND name='context_saves'"
    )
    if cursor.fetchone() is None:
        return False
    cursor = conn.execute("PRAGMA " + alias + ".table_info(context_saves)")
    cols = {row[1] for row in cursor.fetchall()}
    return column in cols


def migrate_legacy(central_db: Path, dry_run: bool = False) -> Dict:
    """掃描 ~ 下所有 legacy per-project DB，合併進中央 DB。

    Args:
        central_db: 中央 DB 路徑
        dry_run: True 則只列出不執行

    Returns:
        {
            "ok": bool,
            "migrated_files": int,     # 成功合併的 legacy DB 數
            "total_inserted": int,     # 新增進中央 DB 的列數
            "skipped_duplicate": int,  # 因去重而跳過的列數（估算）
            "legacy_dbs": [str],       # 本次掃描到的 legacy DB 絕對路徑
            "renamed": [str],          # 改名為 .migrated-YYYYMMDD 的新檔名
            "errors": [{"path":..., "error":...}],
        }
    """
    central_db = Path(central_db)
    legacy_dbs = _scan_legacy_dbs(central_db)

    result = {
        "ok": True,
        "migrated_files": 0,
        "total_inserted": 0,
        "skipped_duplicate": 0,
        "legacy_dbs": [str(p) for p in legacy_dbs],
        "renamed": [],
        "errors": [],
        "dry_run": dry_run,
    }

    if not legacy_dbs:
        return result

    # 確保中央 DB 已初始化
    if not dry_run:
        init_db(central_db)

    stamp = datetime.now().strftime("%Y%m%d")

    if dry_run:
        # dry-run 只掃描並回報預估筆數
        for legacy_db in legacy_dbs:
            try:
                legacy_conn = sqlite3.connect(str(legacy_db))
                try:
                    row = legacy_conn.execute(
                        "SELECT COUNT(*) FROM context_saves"
                    ).fetchone()
                    if row:
                        result["total_inserted"] += int(row[0])
                finally:
                    legacy_conn.close()
            except sqlite3.Error as exc:
                result["errors"].append({"path": str(legacy_db), "error": str(exc)})
        return result

    # 實際合併：逐檔 ATTACH；rename 延後到 COMMIT 之後
    # （SQLite 在 transaction 期間對 attached 檔保有 lock，需釋放後才能 rename）
    to_rename: List[Path] = []
    with _connect_db(central_db, writer=True) as conn:
        for legacy_db in legacy_dbs:
            attached = False
            try:
                fallback = _legacy_fallback_project_path(legacy_db)
                # SQLite 不支援 ? 綁定在 ATTACH 檔名；escape 單引號避免注入
                legacy_path_escaped = str(legacy_db).replace("'", "''")
                conn.execute("ATTACH DATABASE '" + legacy_path_escaped + "' AS legacy")
                attached = True

                # 檢查 legacy 是否有 context_saves 表
                cursor = conn.execute(
                    "SELECT name FROM legacy.sqlite_master "
                    "WHERE type='table' AND name='context_saves'"
                )
                if cursor.fetchone() is None:
                    result["errors"].append({
                        "path": str(legacy_db),
                        "error": "no context_saves table",
                    })
                    continue

                # 判斷 legacy 是否含 project_path / deleted_at 欄位
                has_project = _legacy_has_column(conn, "legacy", "project_path")
                has_deleted = _legacy_has_column(conn, "legacy", "deleted_at")

                project_expr = (
                    "COALESCE(l.project_path, ?)" if has_project else "?"
                )
                deleted_expr = "l.deleted_at" if has_deleted else "NULL"

                # 合併前 / 後總筆數，推算插入與跳過
                before_total = conn.execute(
                    "SELECT COUNT(*) FROM main.context_saves"
                ).fetchone()[0]
                legacy_total = conn.execute(
                    "SELECT COUNT(*) FROM legacy.context_saves"
                ).fetchone()[0]

                insert_sql = (
                    "INSERT INTO main.context_saves ("
                    "    session_id, created_at, project_path,"
                    "    context_percentage, title, category, content,"
                    "    tags, model, deleted_at"
                    ") SELECT "
                    "    l.session_id, l.created_at, " + project_expr + ","
                    "    l.context_percentage, l.title, l.category, l.content,"
                    "    COALESCE(l.tags, ''), COALESCE(l.model, ''), " + deleted_expr + " "
                    "FROM legacy.context_saves AS l "
                    "WHERE NOT EXISTS ("
                    "    SELECT 1 FROM main.context_saves AS m "
                    "    WHERE m.session_id = l.session_id "
                    "      AND m.created_at = l.created_at "
                    "      AND m.title = l.title"
                    ")"
                )
                conn.execute(insert_sql, (fallback,))

                after_total = conn.execute(
                    "SELECT COUNT(*) FROM main.context_saves"
                ).fetchone()[0]

                inserted = after_total - before_total
                skipped = legacy_total - inserted
                if skipped < 0:
                    skipped = 0
                result["total_inserted"] += inserted
                result["skipped_duplicate"] += skipped
                result["migrated_files"] += 1
                to_rename.append(legacy_db)

            except sqlite3.Error as exc:
                result["errors"].append({"path": str(legacy_db), "error": str(exc)})
            finally:
                if attached:
                    try:
                        conn.execute("DETACH DATABASE legacy")
                    except sqlite3.Error:
                        pass

        conn.execute("COMMIT")

    # COMMIT 之後 SQLite 已釋放對 legacy 檔的 lock，此時才能安全改名
    for legacy_db in to_rename:
        try:
            renamed_path = legacy_db.with_name("context.db.migrated-" + stamp)
            if renamed_path.exists():
                suffix = 1
                while True:
                    alt = legacy_db.with_name(
                        "context.db.migrated-" + stamp + "-" + str(suffix)
                    )
                    if not alt.exists():
                        renamed_path = alt
                        break
                    suffix += 1
            legacy_db.rename(renamed_path)
            result["renamed"].append(str(renamed_path))
        except OSError as exc:
            result["errors"].append({"path": str(legacy_db), "error": str(exc)})

    return result


def maybe_auto_migrate() -> Optional[Dict]:
    """首次觸發時自動升級 schema + 合併 legacy DB。

    跳過條件：
      - 環境變數 CTX_SAVE_NO_MIGRATE 有設定
      - ~/.ctx-save/.migration-done 旗標已存在

    合併完成後（即使沒有 legacy）寫入旗標；只有錯誤時才不寫。

    Note:
        此函式也負責「既有中央 DB 的 schema migration」——若使用者從 v1 升級而來，
        中央 DB 可能缺 `deleted_at` / `project_path` 欄位，必須先 migrate schema
        才能讓 list/search 等查詢正常運作。
    """
    if os.environ.get("CTX_SAVE_NO_MIGRATE"):
        return None
    if MIGRATION_FLAG.exists():
        return None
    DEFAULT_CENTRAL_DIR.mkdir(parents=True, exist_ok=True)
    central_db = get_db_path()
    # 先跑 schema migration（冪等），確保中央 DB 欄位齊全
    if central_db.exists():
        try:
            with _connect_db(central_db, writer=True) as conn:
                _run_all_migrations(conn)
                conn.execute("COMMIT")
        except sqlite3.Error as exc:
            return {"ok": False, "errors": [{"phase": "schema_migration", "error": str(exc)}]}
    report = migrate_legacy(central_db, dry_run=False)
    if report.get("ok") and not report.get("errors"):
        try:
            MIGRATION_FLAG.touch()
        except OSError:
            pass
    return report


# ---------------------------------------------------------------------------
# CRUD：save / list / get / search / clean / stats / list_projects
# ---------------------------------------------------------------------------


def save(db_path: Path, records: List[Dict]) -> None:
    """儲存多筆 context 記錄。

    自動：
      - init_db（冪等）
      - 套用 category 白名單（非法改 custom，並把原值附加到 content 前綴）
      - 強制填 project_path（若 record 未帶 → _resolve_project_path()）
      - session_id 格式驗證（拋 ValueError）
    """
    db_path = Path(db_path)
    init_db(db_path)
    default_project = _resolve_project_path()

    with _connect_db(db_path, writer=True) as conn:
        inserted = 0
        for r in records:
            session_id = r.get("session_id", "")
            if not _validate_session_id(session_id):
                conn.execute("ROLLBACK")
                raise ValueError(
                    "invalid session_id: must match ^[A-Za-z0-9_-]{1,64}$"
                )

            # category 白名單
            raw_category = r.get("category", "") or ""
            cleaned_category, original = _sanitize_category(raw_category)
            content = r.get("content", "") or ""
            if original is not None and original:
                content = "[原 category: " + str(original) + "]\n" + content

            # project_path
            project_path = r.get("project_path") or default_project

            conn.execute(
                """
                INSERT INTO context_saves
                    (session_id, project_path, context_percentage,
                     title, category, content, tags, model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    project_path,
                    r.get("context_percentage", 0),
                    r.get("title", ""),
                    cleaned_category,
                    content,
                    r.get("tags", ""),
                    r.get("model", ""),
                ),
            )
            inserted += 1
        conn.execute("COMMIT")

    print(json.dumps(
        {"status": "ok", "inserted": inserted, "db_path": str(db_path)},
        ensure_ascii=False,
    ))


def list_sessions(
    db_path: Path,
    project_path: Optional[str] = None,
    limit: int = 20,
) -> None:
    """列出已保存的 session 快照。limit 超過 500 會被限為 500。"""
    db_path = Path(db_path)
    if not db_path.exists():
        print(json.dumps(
            {"sessions": [], "message": "尚無保存記錄"},
            ensure_ascii=False,
        ))
        return

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 20
    if limit > MAX_LIST_LIMIT:
        limit = MAX_LIST_LIMIT
    if limit <= 0:
        limit = 20

    with _connect_db(db_path) as conn:
        query = """
            SELECT
                session_id,
                MIN(created_at) as created_at,
                GROUP_CONCAT(DISTINCT category) as categories,
                MAX(context_percentage) as context_pct,
                COUNT(*) as item_count,
                GROUP_CONCAT(title, ' | ') as titles,
                project_path
            FROM context_saves
            WHERE deleted_at IS NULL
        """
        params: List = []
        if project_path:
            query += " AND project_path = ?"
            params.append(project_path)
        query += (
            " GROUP BY session_id, project_path "
            "ORDER BY MIN(created_at) DESC LIMIT ?"
        )
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r[0],
            "created_at": r[1],
            "categories": r[2],
            "context_percentage": r[3],
            "item_count": r[4],
            "titles": r[5],
            "project_path": r[6],
        })
    print(json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2))


def get_session(db_path: Path, session_id: str) -> None:
    """取得特定 session 的所有記錄。"""
    if not _validate_session_id(session_id):
        raise ValueError(
            "invalid session_id: must match ^[A-Za-z0-9_-]{1,64}$"
        )

    db_path = Path(db_path)
    if not db_path.exists():
        print(json.dumps(
            {"records": [], "message": "尚無保存記錄"},
            ensure_ascii=False,
        ))
        return

    with _connect_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT category, title, content, tags,
                   context_percentage, created_at, model, project_path
            FROM context_saves
            WHERE session_id = ? AND deleted_at IS NULL
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()

    records = []
    for r in rows:
        records.append({
            "category": r[0],
            "title": r[1],
            "content": r[2],
            "tags": r[3],
            "context_percentage": r[4],
            "created_at": r[5],
            "model": r[6],
            "project_path": r[7],
        })
    print(json.dumps({"records": records}, ensure_ascii=False, indent=2))


def search(
    db_path: Path,
    keyword: str,
    project_path: Optional[str] = None,
) -> None:
    """搜尋記錄。keyword 長度超過 200 字元會拋 ValueError。"""
    if not isinstance(keyword, str) or not keyword:
        raise ValueError("keyword required")
    if len(keyword) > MAX_SEARCH_LEN:
        raise ValueError("keyword too long (max " + str(MAX_SEARCH_LEN) + ")")

    db_path = Path(db_path)
    if not db_path.exists():
        print(json.dumps(
            {"results": [], "message": "尚無保存記錄"},
            ensure_ascii=False,
        ))
        return

    with _connect_db(db_path) as conn:
        query = """
            SELECT session_id, created_at, category, title, content, project_path
            FROM context_saves
            WHERE deleted_at IS NULL
              AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)
        """
        like = "%" + keyword + "%"
        params: List = [like, like, like]
        if project_path:
            query += " AND project_path = ?"
            params.append(project_path)
        query += " ORDER BY created_at DESC LIMIT 50"

        rows = conn.execute(query, params).fetchall()

    results = []
    for r in rows:
        results.append({
            "session_id": r[0],
            "created_at": r[1],
            "category": r[2],
            "title": r[3],
            "content": r[4],
            "project_path": r[5],
        })
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


def clean(db_path: Path, days: int = 30) -> None:
    """清除 N 天前的記錄（硬刪；僅刪除尚未軟刪除的資料）。

    v3 額外保護：
      - is_persistent = 1 的長效筆記永不刪
      - 最近 RESTORE_PROTECT_DAYS 天內被 reinject 過的 snapshot 保留
    """
    db_path = Path(db_path)
    if not db_path.exists():
        print(json.dumps(
            {"deleted": 0, "message": "尚無保存記錄"},
            ensure_ascii=False,
        ))
        return
    now = datetime.now()
    cutoff = (now - timedelta(days=days)).isoformat()
    restore_cutoff = (now - timedelta(days=RESTORE_PROTECT_DAYS)).isoformat()
    with _connect_db(db_path, writer=True) as conn:
        cur = conn.execute(
            "DELETE FROM context_saves "
            "WHERE created_at < ? "
            "  AND deleted_at IS NULL "
            "  AND is_persistent = 0 "
            "  AND (restored_at IS NULL OR restored_at < ?)",
            (cutoff, restore_cutoff),
        )
        count = cur.rowcount
        conn.execute("COMMIT")
    print(json.dumps({"deleted": count, "days": days}, ensure_ascii=False))


def list_projects(db_path: Path) -> List[Dict]:
    """列出所有專案（含筆數、session 數、最後寫入時間）。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with _connect_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT project_path,
                   COUNT(*) as record_count,
                   COUNT(DISTINCT session_id) as session_count,
                   MAX(created_at) as last_saved
            FROM context_saves
            WHERE deleted_at IS NULL
            GROUP BY project_path
            ORDER BY last_saved DESC
            """
        ).fetchall()

    projects = []
    for r in rows:
        project_path = r[0] or "(unknown)"
        # name = basename
        try:
            name = PurePosixPath(project_path).name or project_path
        except (TypeError, ValueError):
            name = project_path
        projects.append({
            "path": project_path,
            "name": name,
            "record_count": r[1],
            "session_count": r[2],
            "last_saved": r[3],
        })
    return projects


def stats(db_path: Path) -> None:
    """統計資訊（含 by_project）。"""
    db_path = Path(db_path)
    if not db_path.exists():
        print(json.dumps({"message": "尚無保存記錄"}, ensure_ascii=False))
        return
    with _connect_db(db_path) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM context_saves WHERE deleted_at IS NULL"
        ).fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM context_saves "
            "WHERE deleted_at IS NULL"
        ).fetchone()[0]
        categories = conn.execute(
            """
            SELECT category, COUNT(*) FROM context_saves
            WHERE deleted_at IS NULL
            GROUP BY category ORDER BY COUNT(*) DESC
            """
        ).fetchall()
        by_project = conn.execute(
            """
            SELECT project_path, COUNT(*) FROM context_saves
            WHERE deleted_at IS NULL
            GROUP BY project_path ORDER BY COUNT(*) DESC
            """
        ).fetchall()
        oldest = conn.execute(
            "SELECT MIN(created_at) FROM context_saves WHERE deleted_at IS NULL"
        ).fetchone()[0]
        newest = conn.execute(
            "SELECT MAX(created_at) FROM context_saves WHERE deleted_at IS NULL"
        ).fetchone()[0]

    print(json.dumps(
        {
            "total_records": total,
            "total_sessions": sessions,
            "categories": {c[0]: c[1] for c in categories},
            "by_project": {p[0]: p[1] for p in by_project},
            "oldest": oldest,
            "newest": newest,
        },
        ensure_ascii=False,
        indent=2,
    ))


# ---------------------------------------------------------------------------
# v3 Facade：Reinject 閉環 / Persistent / Distill / Diagnoses / Compact events
# ---------------------------------------------------------------------------


def find_latest_unrestored_autodump(
    db_path: Union[str, Path],
    session_id: Optional[str] = None,
    project_path: Optional[str] = None,
) -> Optional[Dict]:
    """找最近一筆尚未被 reinject 的 auto-dump 快照。

    用於 SessionStart(matcher=compact) 的還原閉環。篩選條件：
      - category = 'auto-dump'
      - deleted_at IS NULL
      - restored_at IS NULL（未曾被 reinject）
      - 若提供 session_id / project_path 則進一步過濾

    Returns:
        單筆 dict（含 id / content / context_percentage / ...），找不到則回 None。
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return None

    with _connect_db(db_path) as conn:
        # correlated subquery 找對應最新一筆未 reinject 的 compact_event；
        # 舊 DB 若無 context_compact_events 表則 fallback 為 NULL。
        if _has_table(conn, "context_compact_events"):
            event_subquery = (
                "(SELECT e.id FROM context_compact_events e "
                " WHERE e.snapshot_id = cs.id AND e.reinjected = 0 "
                " ORDER BY e.happened_at DESC LIMIT 1) "
                "AS latest_compact_event_id"
            )
        else:
            event_subquery = "NULL AS latest_compact_event_id"

        query = (
            "SELECT cs.id, cs.session_id, cs.created_at, cs.project_path, "
            "       cs.context_percentage, cs.title, cs.category, cs.content, "
            "       cs.tags, cs.model, cs.is_persistent, cs.restored_at, "
            "       cs.distilled_from_id, " + event_subquery + " "
            "FROM context_saves cs "
            "WHERE cs.category = 'auto-dump' "
            "  AND cs.deleted_at IS NULL "
            "  AND cs.restored_at IS NULL"
        )
        params: List = []
        if session_id:
            if not _validate_session_id(session_id):
                return None
            query += " AND cs.session_id = ?"
            params.append(session_id)
        if project_path:
            query += " AND cs.project_path = ?"
            params.append(project_path)
        query += " ORDER BY cs.created_at DESC LIMIT 1"

        row = conn.execute(query, params).fetchone()

    if row is None:
        return None
    return {
        "id": row[0],
        "session_id": row[1],
        "created_at": row[2],
        "project_path": row[3],
        "context_percentage": row[4],
        "title": row[5],
        "category": row[6],
        "content": row[7],
        "tags": row[8],
        "model": row[9],
        "is_persistent": row[10],
        "restored_at": row[11],
        "distilled_from_id": row[12],
        "latest_compact_event_id": row[13],
    }


def mark_restored(db_path: Union[str, Path], save_id: int) -> None:
    """更新 context_saves.restored_at 為目前時間。

    reinject 成功後呼叫，避免同一筆快照在後續 compact 內被反覆注入。
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return
    with _connect_db(db_path, writer=True) as conn:
        conn.execute(
            "UPDATE context_saves SET restored_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (int(save_id),),
        )
        conn.execute("COMMIT")


def mark_compact_event_reinjected(
    db_path: Union[str, Path],
    snapshot_id: Optional[int] = None,
    event_id: Optional[int] = None,
) -> None:
    """標記對應的 compact_event 為已 reinject。

    優先以 event_id 更新（精確一筆）；若未給 event_id 則退回原
    「WHERE snapshot_id=? ORDER BY happened_at DESC LIMIT 1」行為
    （v2 呼叫者相容）。兩者皆未給則 no-op。

    event_id 來源：find_latest_unrestored_autodump() 回傳的
    `latest_compact_event_id`（v3.0.1 新增）。對齊此欄位可避免
    snapshot_id 對多筆 compact_event 的邊界情境標錯列。
    """
    if event_id is None and snapshot_id is None:
        return
    db_path = Path(db_path)
    if not db_path.exists():
        return
    try:
        with _connect_db(db_path, writer=True) as conn:
            if not _has_table(conn, "context_compact_events"):
                # migration 尚未跑到 compact_events；no-op 即可
                conn.execute("ROLLBACK")
                return
            if event_id is not None:
                conn.execute(
                    "UPDATE context_compact_events SET reinjected = 1 "
                    "WHERE id = ?",
                    (int(event_id),),
                )
            else:
                conn.execute(
                    "UPDATE context_compact_events SET reinjected = 1 "
                    "WHERE id = ("
                    "  SELECT id FROM context_compact_events "
                    "  WHERE snapshot_id = ? AND reinjected = 0 "
                    "  ORDER BY happened_at DESC LIMIT 1"
                    ")",
                    (int(snapshot_id),),
                )
            conn.execute("COMMIT")
    except sqlite3.OperationalError:
        # 視同 no-op — 呼叫端（ctx-reinject.py）會把例外寫 error log
        return


def pin_save(
    db_path: Union[str, Path],
    save_id: int,
    pinned: bool = True,
) -> None:
    """切換 context_saves.is_persistent 旗標。

    pinned=True → 設為 1（長效）；pinned=False → 設為 0（恢復可刪）。
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return
    value = 1 if pinned else 0
    with _connect_db(db_path, writer=True) as conn:
        conn.execute(
            "UPDATE context_saves SET is_persistent = ? WHERE id = ?",
            (value, int(save_id)),
        )
        conn.execute("COMMIT")


def list_persistent(
    db_path: Union[str, Path],
    project_path: Optional[str] = None,
) -> List[Dict]:
    """列出所有長效筆記（is_persistent=1）。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    query = (
        "SELECT id, session_id, created_at, project_path, "
        "       context_percentage, title, category, tags, model, restored_at "
        "FROM context_saves "
        "WHERE is_persistent = 1 AND deleted_at IS NULL"
    )
    params: List = []
    if project_path:
        query += " AND project_path = ?"
        params.append(project_path)
    query += " ORDER BY created_at DESC"
    with _connect_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "id": r[0],
            "session_id": r[1],
            "created_at": r[2],
            "project_path": r[3],
            "context_percentage": r[4],
            "title": r[5],
            "category": r[6],
            "tags": r[7],
            "model": r[8],
            "restored_at": r[9],
        }
        for r in rows
    ]


def save_distilled(
    db_path: Union[str, Path],
    source_id: int,
    title: str,
    content: str,
    tags: str = "distilled",
) -> int:
    """寫入一筆由 distill 產生的瘦身版快照，回傳新 id。

    繼承 source 的 session_id / project_path / context_percentage，
    category 固定為 'distilled'，並設定 distilled_from_id 指向原筆 id。
    """
    db_path = Path(db_path)
    init_db(db_path)
    with _connect_db(db_path, writer=True) as conn:
        row = conn.execute(
            "SELECT session_id, project_path, context_percentage "
            "FROM context_saves WHERE id = ?",
            (int(source_id),),
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            raise ValueError("source_id not found: " + str(source_id))
        session_id, project_path, context_pct = row
        cur = conn.execute(
            """
            INSERT INTO context_saves
                (session_id, project_path, context_percentage,
                 title, category, content, tags, model,
                 is_persistent, distilled_from_id)
            VALUES (?, ?, ?, ?, 'distilled', ?, ?, '', 0, ?)
            """,
            (
                session_id,
                project_path,
                context_pct,
                title,
                content,
                tags,
                int(source_id),
            ),
        )
        new_id = cur.lastrowid
        conn.execute("COMMIT")
    return int(new_id) if new_id is not None else 0


def list_distilled_from(
    db_path: Union[str, Path],
    source_id: int,
) -> List[Dict]:
    """列出由特定 source_id 衍生的所有 distilled 筆記。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with _connect_db(db_path) as conn:
        rows = conn.execute(
            "SELECT id, session_id, created_at, project_path, "
            "       title, tags, is_persistent "
            "FROM context_saves "
            "WHERE distilled_from_id = ? AND deleted_at IS NULL "
            "ORDER BY created_at DESC",
            (int(source_id),),
        ).fetchall()
    return [
        {
            "id": r[0],
            "session_id": r[1],
            "created_at": r[2],
            "project_path": r[3],
            "title": r[4],
            "tags": r[5],
            "is_persistent": r[6],
        }
        for r in rows
    ]


def save_diagnosis(
    db_path: Union[str, Path],
    diagnosis: Dict,
) -> int:
    """寫入一筆 ctx-analyze 診斷結果，回傳新 id。

    預期 diagnosis dict 至少含：
      session_id, project_path, context_percentage, score,
      tokens_conversation, tokens_files, tokens_tools_schema,
      tokens_tools_runtime, tokens_system, findings（list 或 JSON 字串）,
      suggest_focus
    """
    db_path = Path(db_path)
    init_db(db_path)

    session_id = diagnosis.get("session_id", "")
    if not _validate_session_id(session_id):
        raise ValueError(
            "invalid session_id: must match ^[A-Za-z0-9_-]{1,64}$"
        )

    findings = diagnosis.get("findings", [])
    if isinstance(findings, (list, dict)):
        findings_json = json.dumps(findings, ensure_ascii=False)
    elif isinstance(findings, str):
        findings_json = findings
    else:
        findings_json = "[]"

    with _connect_db(db_path, writer=True) as conn:
        cur = conn.execute(
            """
            INSERT INTO context_diagnoses
                (session_id, project_path, context_percentage, score,
                 tokens_conversation, tokens_files, tokens_tools_schema,
                 tokens_tools_runtime, tokens_system,
                 findings_json, suggest_focus)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                diagnosis.get("project_path") or _resolve_project_path(),
                diagnosis.get("context_percentage"),
                diagnosis.get("score"),
                diagnosis.get("tokens_conversation"),
                diagnosis.get("tokens_files"),
                diagnosis.get("tokens_tools_schema"),
                diagnosis.get("tokens_tools_runtime"),
                diagnosis.get("tokens_system"),
                findings_json,
                diagnosis.get("suggest_focus"),
            ),
        )
        new_id = cur.lastrowid
        conn.execute("COMMIT")
    return int(new_id) if new_id is not None else 0


def list_diagnoses(
    db_path: Union[str, Path],
    session_id: Optional[str] = None,
    project_path: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """列出診斷紀錄。預設按 ran_at DESC，不回傳 findings_json 以降低 payload。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    if limit > MAX_LIST_LIMIT:
        limit = MAX_LIST_LIMIT
    if limit <= 0:
        limit = 50

    query = (
        "SELECT id, session_id, project_path, ran_at, context_percentage, "
        "       score, tokens_conversation, tokens_files, "
        "       tokens_tools_schema, tokens_tools_runtime, tokens_system, "
        "       suggest_focus "
        "FROM context_diagnoses "
        "WHERE deleted_at IS NULL"
    )
    params: List = []
    if session_id:
        if not _validate_session_id(session_id):
            return []
        query += " AND session_id = ?"
        params.append(session_id)
    if project_path:
        query += " AND project_path = ?"
        params.append(project_path)
    query += " ORDER BY ran_at DESC LIMIT ?"
    params.append(limit)

    with _connect_db(db_path) as conn:
        rows = conn.execute(query, params).fetchall()

    return [
        {
            "id": r[0],
            "session_id": r[1],
            "project_path": r[2],
            "ran_at": r[3],
            "context_percentage": r[4],
            "score": r[5],
            "tokens_conversation": r[6],
            "tokens_files": r[7],
            "tokens_tools_schema": r[8],
            "tokens_tools_runtime": r[9],
            "tokens_system": r[10],
            "suggest_focus": r[11],
        }
        for r in rows
    ]


def get_diagnosis(
    db_path: Union[str, Path],
    diag_id: int,
) -> Optional[Dict]:
    """取得單筆診斷結果（含 findings_json 解析後的 list）。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    with _connect_db(db_path) as conn:
        row = conn.execute(
            "SELECT id, session_id, project_path, ran_at, context_percentage, "
            "       score, tokens_conversation, tokens_files, "
            "       tokens_tools_schema, tokens_tools_runtime, tokens_system, "
            "       findings_json, suggest_focus "
            "FROM context_diagnoses "
            "WHERE id = ? AND deleted_at IS NULL",
            (int(diag_id),),
        ).fetchone()
    if row is None:
        return None
    try:
        findings = json.loads(row[11]) if row[11] else []
    except json.JSONDecodeError:
        findings = []
    return {
        "id": row[0],
        "session_id": row[1],
        "project_path": row[2],
        "ran_at": row[3],
        "context_percentage": row[4],
        "score": row[5],
        "tokens_conversation": row[6],
        "tokens_files": row[7],
        "tokens_tools_schema": row[8],
        "tokens_tools_runtime": row[9],
        "tokens_system": row[10],
        "findings": findings,
        "suggest_focus": row[12],
    }


def delete_diagnosis(
    db_path: Union[str, Path],
    diag_id: int,
) -> None:
    """軟刪除一筆診斷結果（設 deleted_at）。"""
    db_path = Path(db_path)
    if not db_path.exists():
        return
    with _connect_db(db_path, writer=True) as conn:
        conn.execute(
            "UPDATE context_diagnoses SET deleted_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND deleted_at IS NULL",
            (int(diag_id),),
        )
        conn.execute("COMMIT")


def record_compact_event(
    db_path: Union[str, Path],
    session_id: str,
    project_path: str,
    happened_at: Union[str, datetime],
    trigger: Optional[str],
    context_pct: Optional[float],
    snapshot_id: Optional[int],
) -> int:
    """寫入一筆 compact 事件到 context_compact_events，回傳新 id。

    ctx-autodump.py 在 save 成功後呼叫；reinjected 欄位先為 0，
    之後由 ctx-reinject.py 透過 mark_compact_event_reinjected 更新。

    happened_at 可為 ISO 字串或 datetime 物件，會統一轉成 ISO 字串。
    """
    db_path = Path(db_path)
    init_db(db_path)

    if isinstance(happened_at, datetime):
        happened_at_str = happened_at.isoformat(timespec="seconds")
    else:
        happened_at_str = str(happened_at) if happened_at else \
            datetime.now().isoformat(timespec="seconds")

    with _connect_db(db_path, writer=True) as conn:
        cur = conn.execute(
            "INSERT INTO context_compact_events "
            "(session_id, project_path, happened_at, trigger, "
            " context_pct, snapshot_id, reinjected) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                session_id,
                project_path or "(unknown)",
                happened_at_str,
                trigger,
                context_pct,
                int(snapshot_id) if snapshot_id is not None else None,
            ),
        )
        new_id = cur.lastrowid
        conn.execute("COMMIT")
    return int(new_id) if new_id is not None else 0


def list_compact_events(
    db_path: Union[str, Path],
    session_id: Optional[str] = None,
    days: int = 30,
    limit: int = 200,
) -> List[Dict]:
    """列出近 N 天的 compact 事件，供 Timeline / events CLI 使用。

    對「DB 不存在 / 表不存在 / 查詢失敗」一律回 []，避免 Web Viewer
    首次載入未 migration 的 DB 時收 500。
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    if limit > MAX_LIST_LIMIT:
        limit = MAX_LIST_LIMIT
    if limit <= 0:
        limit = 200

    cutoff = (datetime.now() - timedelta(days=max(days, 1))).isoformat()
    query = (
        "SELECT id, session_id, project_path, happened_at, trigger, "
        "       context_pct, snapshot_id, reinjected "
        "FROM context_compact_events "
        "WHERE deleted_at IS NULL AND happened_at >= ?"
    )
    params: List = [cutoff]
    if session_id:
        if not _validate_session_id(session_id):
            return []
        query += " AND session_id = ?"
        params.append(session_id)
    query += " ORDER BY happened_at DESC LIMIT ?"
    params.append(limit)

    try:
        with _connect_db(db_path) as conn:
            if not _has_table(conn, "context_compact_events"):
                return []
            rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError:
        # 表結構不完整或 migration 中途，視同空；由呼叫端決定是否提示
        return []

    return [
        {
            "id": r[0],
            "session_id": r[1],
            "project_path": r[2],
            "happened_at": r[3],
            "trigger": r[4],
            "context_pct": r[5],
            "snapshot_id": r[6],
            "reinjected": r[7],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# CLI entries
# ---------------------------------------------------------------------------


def _cli_migrate_legacy(argv: List[str]) -> int:
    """`migrate-legacy [--dry-run] [--yes]` 子指令 entry。"""
    dry_run = "--dry-run" in argv
    central_db = get_db_path()
    report = migrate_legacy(central_db, dry_run=dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 成功（即使沒有 legacy）也寫 flag，避免下次再掃
    if not dry_run and report.get("ok") and not report.get("errors"):
        try:
            DEFAULT_CENTRAL_DIR.mkdir(parents=True, exist_ok=True)
            MIGRATION_FLAG.touch()
        except OSError:
            pass
    return 0 if report.get("ok") else 1


def _main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    cmd = argv[1]
    db_path = get_db_path()

    # 讀類指令先嘗試 auto-migrate
    read_cmds = {"list", "list-projects", "get", "search", "stats"}
    if cmd in read_cmds:
        maybe_auto_migrate()

    if cmd == "init":
        path = init_db(db_path)
        print(json.dumps(
            {"status": "ok", "db_path": path},
            ensure_ascii=False,
        ))
        return 0

    if cmd == "migrate":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if not db_path.exists():
            init_db(db_path)
            print("✅ 已建立中央 DB 並套用最新 schema")
        else:
            with _connect_db(db_path, writer=True) as conn:
                _init_schema(conn)
                migrated = _run_all_migrations(conn)
                conn.execute("COMMIT")
            if any(migrated.values()):
                changed = [k for k, v in migrated.items() if v]
                print("✅ 已升級：" + ", ".join(changed))
            else:
                print("ℹ 已是最新版")
        return 0

    if cmd == "migrate-legacy":
        return _cli_migrate_legacy(argv)

    if cmd == "save":
        data = json.load(sys.stdin)
        records = data if isinstance(data, list) else [data]
        try:
            save(db_path, records)
        except ValueError as exc:
            print(json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            ), file=sys.stderr)
            return 2
        return 0

    if cmd == "list":
        limit = int(argv[2]) if len(argv) > 2 else 20
        # 無特定 project 篩選，列全部（集中式 DB 的慣用行為）
        list_sessions(db_path, project_path=None, limit=limit)
        return 0

    if cmd == "list-projects":
        projects = list_projects(db_path)
        print(json.dumps({"projects": projects}, ensure_ascii=False, indent=2))
        return 0

    if cmd == "get":
        if len(argv) < 3:
            print("用法: ctx-db.py get <session_id>", file=sys.stderr)
            return 1
        try:
            get_session(db_path, argv[2])
        except ValueError as exc:
            print(json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            ), file=sys.stderr)
            return 2
        return 0

    if cmd == "search":
        if len(argv) < 3:
            print("用法: ctx-db.py search <keyword>", file=sys.stderr)
            return 1
        try:
            search(db_path, argv[2], project_path=None)
        except ValueError as exc:
            print(json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            ), file=sys.stderr)
            return 2
        return 0

    if cmd == "clean":
        days = int(argv[2]) if len(argv) > 2 else 30
        clean(db_path, days)
        return 0

    if cmd == "stats":
        stats(db_path)
        return 0

    print("未知指令: " + cmd, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
