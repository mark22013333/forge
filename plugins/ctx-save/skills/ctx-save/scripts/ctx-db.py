#!/usr/bin/env python3
"""ctx-db.py — Context Save SQLite 操作腳本

用法:
    ctx-db.py init                    初始化資料庫
    ctx-db.py migrate                 執行資料庫遷移（冪等）
    ctx-db.py save                    儲存記錄（從 stdin 讀取 JSON）
    ctx-db.py list [limit]            列出快照（預設 20）
    ctx-db.py get <session_id>        取得特定 session 的所有記錄
    ctx-db.py search <keyword>        搜尋關鍵字
    ctx-db.py clean [days]            清除 N 天前的記錄（預設 30）
    ctx-db.py stats                   統計資訊

環境變數:
    CTX_PROJECT  專案根目錄（預設為 cwd）
"""

import json
import sqlite3
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

DB_NAME = "context.db"


def get_db_path(project_root=None):
    root = project_root or os.getcwd()
    return os.path.join(root, ".ctx-save", DB_NAME)


def migrate_add_deleted_at(conn) -> bool:
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
    conn.commit()
    return True


def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_saves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            project_path TEXT NOT NULL,
            context_percentage REAL,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            model TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_saves_session
        ON context_saves(session_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_saves_project
        ON context_saves(project_path)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_saves_created
        ON context_saves(created_at DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_saves_category
        ON context_saves(category)
    """)
    conn.commit()
    # 確保既有資料庫也會被升級（冪等）
    migrate_add_deleted_at(conn)
    conn.close()
    return db_path


def save(db_path, records):
    """儲存多筆 context 記錄"""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    inserted = 0
    for r in records:
        conn.execute("""
            INSERT INTO context_saves
                (session_id, project_path, context_percentage, title, category, content, tags, model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r.get("session_id", ""),
            r.get("project_path", ""),
            r.get("context_percentage", 0),
            r.get("title", ""),
            r.get("category", ""),
            r.get("content", ""),
            r.get("tags", ""),
            r.get("model", ""),
        ))
        inserted += 1
    conn.commit()
    conn.close()
    print(json.dumps({"status": "ok", "inserted": inserted}, ensure_ascii=False))


def list_sessions(db_path, project_path=None, limit=20):
    """列出已保存的 session 快照"""
    if not os.path.exists(db_path):
        print(json.dumps({"sessions": [], "message": "尚無保存記錄"}, ensure_ascii=False))
        return
    conn = sqlite3.connect(db_path)
    query = """
        SELECT
            session_id,
            MIN(created_at) as created_at,
            GROUP_CONCAT(DISTINCT category) as categories,
            MAX(context_percentage) as context_pct,
            COUNT(*) as item_count,
            GROUP_CONCAT(title, ' | ') as titles
        FROM context_saves
        WHERE deleted_at IS NULL
    """
    params = []
    if project_path:
        query += " AND project_path = ?"
        params.append(project_path)
    query += " GROUP BY session_id ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    sessions = []
    for r in rows:
        sessions.append({
            "session_id": r[0],
            "created_at": r[1],
            "categories": r[2],
            "context_percentage": r[3],
            "item_count": r[4],
            "titles": r[5],
        })
    print(json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2))


def get_session(db_path, session_id):
    """取得特定 session 的所有記錄"""
    if not os.path.exists(db_path):
        print(json.dumps({"records": [], "message": "尚無保存記錄"}, ensure_ascii=False))
        return
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT category, title, content, tags, context_percentage, created_at, model
        FROM context_saves
        WHERE session_id = ? AND deleted_at IS NULL
        ORDER BY id
    """, (session_id,)).fetchall()
    conn.close()

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
        })
    print(json.dumps({"records": records}, ensure_ascii=False, indent=2))


def search(db_path, keyword, project_path=None):
    """搜尋記錄"""
    if not os.path.exists(db_path):
        print(json.dumps({"results": [], "message": "尚無保存記錄"}, ensure_ascii=False))
        return
    conn = sqlite3.connect(db_path)
    query = """
        SELECT session_id, created_at, category, title, content
        FROM context_saves
        WHERE deleted_at IS NULL
          AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)
    """
    like = f"%{keyword}%"
    params = [like, like, like]
    if project_path:
        query += " AND project_path = ?"
        params.append(project_path)
    query += " ORDER BY created_at DESC LIMIT 20"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "session_id": r[0],
            "created_at": r[1],
            "category": r[2],
            "title": r[3],
            "content": r[4],
        })
    print(json.dumps({"results": results}, ensure_ascii=False, indent=2))


def clean(db_path, days=30):
    """清除 N 天前的記錄"""
    if not os.path.exists(db_path):
        print(json.dumps({"deleted": 0, "message": "尚無保存記錄"}, ensure_ascii=False))
        return
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    # 僅硬刪尚未軟刪除的記錄，避免重複處理已 soft delete 的資料
    result = conn.execute(
        "DELETE FROM context_saves WHERE created_at < ? AND deleted_at IS NULL",
        (cutoff,),
    )
    count = result.rowcount
    conn.commit()
    conn.close()
    print(json.dumps({"deleted": count, "days": days}, ensure_ascii=False))


def stats(db_path):
    """統計資訊"""
    if not os.path.exists(db_path):
        print(json.dumps({"message": "尚無保存記錄"}, ensure_ascii=False))
        return
    conn = sqlite3.connect(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM context_saves WHERE deleted_at IS NULL"
    ).fetchone()[0]
    sessions = conn.execute(
        "SELECT COUNT(DISTINCT session_id) FROM context_saves WHERE deleted_at IS NULL"
    ).fetchone()[0]
    categories = conn.execute("""
        SELECT category, COUNT(*) FROM context_saves
        WHERE deleted_at IS NULL
        GROUP BY category ORDER BY COUNT(*) DESC
    """).fetchall()
    oldest = conn.execute(
        "SELECT MIN(created_at) FROM context_saves WHERE deleted_at IS NULL"
    ).fetchone()[0]
    newest = conn.execute(
        "SELECT MAX(created_at) FROM context_saves WHERE deleted_at IS NULL"
    ).fetchone()[0]
    conn.close()

    print(json.dumps({
        "total_records": total,
        "total_sessions": sessions,
        "categories": {c[0]: c[1] for c in categories},
        "oldest": oldest,
        "newest": newest,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    project = os.environ.get("CTX_PROJECT", os.getcwd())
    db_path = get_db_path(project)

    if cmd == "init":
        path = init_db(db_path)
        print(json.dumps({"status": "ok", "db_path": path}, ensure_ascii=False))

    elif cmd == "migrate":
        # 若 DB 檔不存在則先建立
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        if not os.path.exists(db_path):
            init_db(db_path)
            print("✅ 已升級：新增 deleted_at 欄位")
        else:
            conn = sqlite3.connect(db_path)
            try:
                migrated = migrate_add_deleted_at(conn)
            finally:
                conn.close()
            if migrated:
                print("✅ 已升級：新增 deleted_at 欄位")
            else:
                print("ℹ 已是最新版")

    elif cmd == "save":
        data = json.load(sys.stdin)
        records = data if isinstance(data, list) else [data]
        save(db_path, records)

    elif cmd == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        list_sessions(db_path, project_path=project, limit=limit)

    elif cmd == "get":
        if len(sys.argv) < 3:
            print("用法: ctx-db.py get <session_id>", file=sys.stderr)
            sys.exit(1)
        get_session(db_path, sys.argv[2])

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("用法: ctx-db.py search <keyword>", file=sys.stderr)
            sys.exit(1)
        search(db_path, sys.argv[2], project_path=project)

    elif cmd == "clean":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        clean(db_path, days)

    elif cmd == "stats":
        stats(db_path)

    else:
        print(f"未知指令: {cmd}", file=sys.stderr)
        sys.exit(1)
