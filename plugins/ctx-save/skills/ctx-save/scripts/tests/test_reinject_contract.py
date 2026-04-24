"""test_reinject_contract.py — Hook 契約（arch.md §2.4）。

三情境 ctx-reinject.py 必須 exit 0（永不中斷 Claude Code）：
  1. CTX_SAVE_DB_PATH 指向不存在的檔
  2. SQLite lock（同檔被 BEGIN EXCLUSIVE 佔住）
  3. DB 已 init 但找不到 unrestored snapshot

若 plugin-level 的 ctx-reinject.py 尚未完成，整個 TestCase skip。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from _loader import load_ctx_db  # noqa: E402


# plugin 層 ctx-reinject.py 路徑
PLUGIN_ROOT = HERE.parent.parent.parent.parent  # skills/ctx-save → ctx-save plugin
REINJECT_SCRIPT = PLUGIN_ROOT / "scripts" / "ctx-reinject.py"


def _unique_session_id() -> str:
    """每個 test 用獨立 session_id，避開 cooldown 相互干擾。"""
    return "sess-" + uuid.uuid4().hex[:16]


@unittest.skipUnless(
    REINJECT_SCRIPT.exists(),
    f"ctx-reinject.py not found at {REINJECT_SCRIPT}; plugin layer still WIP",
)
class ReinjectHookContractTest(unittest.TestCase):
    """三情境都必須 exit 0。"""

    def _run_reinject(
        self,
        db_path: Path,
        session_id: str,
        timeout: float = 5.0,
    ) -> subprocess.CompletedProcess:
        """以 subprocess 跑 ctx-reinject.py，指定 CTX_SAVE_DB_PATH。"""
        env = os.environ.copy()
        env["CTX_SAVE_DB_PATH"] = str(db_path)
        # 避免走到 legacy migration 掃家目錄
        env["CTX_SAVE_NO_MIGRATE"] = "1"
        payload = json.dumps({
            "session_id": session_id,
            "hook_event_name": "SessionStart",
            "source": "compact",
        })
        return subprocess.run(
            [sys.executable, str(REINJECT_SCRIPT)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )

    def test_db_file_missing_exit_zero(self) -> None:
        """DB 檔不存在 → exit 0（合理情境：尚未累積資料）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_db = Path(tmpdir) / "nonexistent.db"
            self.assertFalse(fake_db.exists())
            result = self._run_reinject(fake_db, _unique_session_id())
            self.assertEqual(
                result.returncode, 0,
                msg=f"stderr={result.stderr!r}",
            )

    def test_sqlite_locked_exit_zero(self) -> None:
        """SQLite 被另一條 connection 鎖住時，reinject 必須 exit 0 而不丟例外。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "locked.db"
            # 初始化 schema
            ctx_db = load_ctx_db()
            ctx_db.init_db(db_path)

            # 在另一條 connection 拿 EXCLUSIVE lock，同步阻塞 reinject 的讀取嘗試
            blocker = sqlite3.connect(str(db_path), timeout=0.1)
            try:
                blocker.execute("BEGIN EXCLUSIVE")
                # 立即呼叫 reinject；busy_timeout 超過後 SQLite 會噴 OperationalError
                # reinject 契約要求吞例外 exit 0
                result = self._run_reinject(
                    db_path,
                    _unique_session_id(),
                    timeout=10.0,
                )
                self.assertEqual(
                    result.returncode, 0,
                    msg=f"stderr={result.stderr!r}",
                )
            finally:
                try:
                    blocker.rollback()
                finally:
                    blocker.close()

    def test_no_unrestored_snapshot_exit_zero(self) -> None:
        """DB 存在但找不到 unrestored auto-dump → exit 0（silent）。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "empty.db"
            ctx_db = load_ctx_db()
            ctx_db.init_db(db_path)
            # 刻意不寫入任何 auto-dump
            result = self._run_reinject(db_path, _unique_session_id())
            self.assertEqual(
                result.returncode, 0,
                msg=f"stderr={result.stderr!r}",
            )
            # 沒 snapshot 時不應該注入任何內容到 stderr
            # （error log 走檔案不走 stderr）
            self.assertEqual(
                result.stderr.strip(), "",
                msg=f"預期 silent exit，實際 stderr={result.stderr!r}",
            )

    def test_empty_stdin_exit_zero(self) -> None:
        """stdin 完全空（異常呼叫）→ 仍 exit 0。"""
        env = os.environ.copy()
        env["CTX_SAVE_NO_MIGRATE"] = "1"
        result = subprocess.run(
            [sys.executable, str(REINJECT_SCRIPT)],
            input="",
            capture_output=True,
            text=True,
            env=env,
            timeout=5.0,
        )
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
