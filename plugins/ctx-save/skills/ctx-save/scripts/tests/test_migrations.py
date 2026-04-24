"""test_migrations.py — v3 schema migration 冪等性與欄位存在驗證。

對應 arch.md §10（Migration idempotency）與 db.md §7。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from _loader import load_ctx_db  # noqa: E402


class MigrationIdempotencyTest(unittest.TestCase):
    """對新 DB 跑 init_db 兩次，第二次所有 migrate_* 必須回 False。"""

    def setUp(self) -> None:
        self.ctx_db = load_ctx_db()
        self.tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".db"
        )
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

    def tearDown(self) -> None:
        try:
            self.db_path.unlink()
        except FileNotFoundError:
            pass

    def test_init_schema_idempotent(self) -> None:
        """連續跑兩次 init_db，第二次每個 migrate_* 都應回 False。"""
        self.ctx_db.init_db(self.db_path)

        # 第二次手動觸發 migrations 並驗回傳
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = self.ctx_db._run_all_migrations(conn)
            conn.execute("COMMIT")
        finally:
            conn.close()

        self.assertTrue(
            all(value is False for value in result.values()),
            msg=f"第二次 migration 仍有實際動作：{result}",
        )

    def test_v3_columns_present(self) -> None:
        """init_db 後驗 is_persistent / restored_at / distilled_from_id 欄位存在。"""
        self.ctx_db.init_db(self.db_path)

        conn = sqlite3.connect(str(self.db_path))
        try:
            rows = conn.execute("PRAGMA table_info(context_saves)").fetchall()
        finally:
            conn.close()

        names = {row[1] for row in rows}
        for required in ("is_persistent", "restored_at", "distilled_from_id"):
            self.assertIn(required, names)

    def test_diagnoses_table_created(self) -> None:
        """驗 context_diagnoses 表與相關索引存在。"""
        self.ctx_db.init_db(self.db_path)

        conn = sqlite3.connect(str(self.db_path))
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            indexes = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("context_diagnoses", tables)
        # db.md §5.2 指定的索引
        self.assertIn("idx_diag_session", indexes)
        self.assertIn("idx_diag_ran", indexes)

    def test_compact_events_table_created(self) -> None:
        """驗 context_compact_events 表與索引存在。"""
        self.ctx_db.init_db(self.db_path)

        conn = sqlite3.connect(str(self.db_path))
        try:
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            indexes = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("context_compact_events", tables)
        self.assertIn("idx_compact_happened", indexes)
        self.assertIn("idx_compact_session", indexes)


class MigrationFromLegacyTest(unittest.TestCase):
    """舊 DB 缺 v3 欄位時，第一次跑 migration 應實際執行，第二次冪等。"""

    def setUp(self) -> None:
        self.ctx_db = load_ctx_db()
        self.tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".db"
        )
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

        # 手動建立一個 v2 時代的 schema（缺 v3 欄位、無 v3 表）
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                """
                CREATE TABLE context_saves (
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
                    deleted_at DATETIME
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        try:
            self.db_path.unlink()
        except FileNotFoundError:
            pass

    def test_first_run_upgrades_then_idempotent(self) -> None:
        """第一次跑會加欄位建表，第二次全 False。"""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("BEGIN IMMEDIATE")
            first = self.ctx_db._run_all_migrations(conn)
            conn.execute("COMMIT")

            conn.execute("BEGIN IMMEDIATE")
            second = self.ctx_db._run_all_migrations(conn)
            conn.execute("COMMIT")
        finally:
            conn.close()

        # 第一次：persistent / restored_at / distilled_from_id / 兩個表至少有實際動作
        self.assertTrue(
            first["persistent"] or first["restored_at"]
            or first["distilled_from_id"]
            or first["diagnoses_table"]
            or first["compact_events_table"]
        )
        # 第二次：應全 False
        self.assertTrue(all(v is False for v in second.values()))


if __name__ == "__main__":
    unittest.main()
