"""test_classifier.py

驗 `classify()` 把 events 正確歸到五類、SessionBaseline 欄位正確。
- 五類合計 == sum(tokens_approx)
- files_avg / files_p80 僅統計 kind='file_read' 事件
- recent_turns_window 只取最近 20 輪 user/assistant text
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze.classifier import SessionBaseline, TokenBreakdown, classify  # noqa: E402
from analyze.scanner import Event, scan  # noqa: E402


def _ev(idx: int, role: str, kind: str, tokens: int, **meta) -> Event:
    return Event(
        idx=idx,
        ts=datetime(2026, 4, 21, 10, 0, idx % 60),
        role=role,
        kind=kind,
        tokens_approx=tokens,
        meta=meta,
    )


class TestClassifierFiveBuckets(unittest.TestCase):
    """五類 token 歸類：conversation / files / tools_schema / tools_runtime / system。"""

    def test_empty_events_gives_zeros(self) -> None:
        tokens, baseline = classify([])
        self.assertEqual(tokens, TokenBreakdown(0, 0, 0, 0, 0))
        self.assertEqual(baseline.total_tokens, 0)
        self.assertEqual(baseline.files_avg_tokens, 0.0)
        self.assertEqual(baseline.files_p80_tokens, 0.0)
        self.assertEqual(baseline.recent_turns_window, ())

    def test_conversation_counts_user_and_assistant_text(self) -> None:
        evs = [
            _ev(0, "user", "text", 100),
            _ev(1, "assistant", "text", 200),
        ]
        t, _ = classify(evs)
        self.assertEqual(t.conversation, 300)
        self.assertEqual(t.files, 0)

    def test_file_read_kind_goes_to_files(self) -> None:
        evs = [
            _ev(0, "assistant", "file_read", 500,
                tool_name="Read", file_path="/a.py"),
        ]
        t, _ = classify(evs)
        self.assertEqual(t.files, 500)
        self.assertEqual(t.tools_runtime, 0)

    def test_read_grep_glob_tool_use_go_to_files(self) -> None:
        """builtin Read/Grep/Glob 的 tool_use/tool_result 也算 files。"""
        evs = [
            _ev(0, "assistant", "tool_use", 100, tool_name="Grep"),
            _ev(1, "tool", "tool_result", 200, tool_name="Grep"),
            _ev(2, "assistant", "tool_use", 50, tool_name="Glob"),
        ]
        t, _ = classify(evs)
        self.assertEqual(t.files, 350)
        self.assertEqual(t.tools_runtime, 0)

    def test_bash_and_mcp_go_to_tools_runtime(self) -> None:
        evs = [
            _ev(0, "assistant", "tool_use", 80, tool_name="Bash"),
            _ev(1, "tool", "tool_result", 120, tool_name="Bash"),
            _ev(2, "assistant", "tool_use", 40, tool_name="mcp__foo__bar"),
        ]
        t, _ = classify(evs)
        self.assertEqual(t.tools_runtime, 240)
        self.assertEqual(t.files, 0)

    def test_system_role_goes_to_system_bucket(self) -> None:
        evs = [
            _ev(0, "system", "text", 500),
        ]
        t, _ = classify(evs)
        self.assertEqual(t.system, 500)
        self.assertEqual(t.tools_schema, 0)

    def test_tool_schema_metadata_goes_to_tools_schema(self) -> None:
        evs = [
            _ev(0, "system", "text", 700, is_tool_schema=True),
        ]
        t, _ = classify(evs)
        self.assertEqual(t.tools_schema, 700)
        self.assertEqual(t.system, 0)

    def test_total_sum_preserved(self) -> None:
        evs = [
            _ev(0, "user", "text", 100),
            _ev(1, "assistant", "file_read", 200, tool_name="Read",
                file_path="/a.py"),
            _ev(2, "assistant", "tool_use", 50, tool_name="Bash"),
            _ev(3, "system", "text", 30),
            _ev(4, "system", "text", 40, is_tool_schema=True),
        ]
        t, b = classify(evs)
        total = (t.conversation + t.files + t.tools_schema
                 + t.tools_runtime + t.system)
        self.assertEqual(total, 420)
        self.assertEqual(b.total_tokens, 420)


class TestSessionBaseline(unittest.TestCase):
    """SessionBaseline 的統計欄位。"""

    def test_files_avg_uses_file_read_events_only(self) -> None:
        evs = [
            _ev(0, "assistant", "file_read", 100, tool_name="Read",
                file_path="/a.py"),
            _ev(1, "assistant", "file_read", 300, tool_name="Read",
                file_path="/b.py"),
            _ev(2, "assistant", "tool_use", 999, tool_name="Bash"),
        ]
        _, b = classify(evs)
        self.assertAlmostEqual(b.files_avg_tokens, 200.0)

    def test_files_p80_ordering(self) -> None:
        evs = [
            _ev(i, "assistant", "file_read", v, tool_name="Read",
                file_path=f"/f{i}.py")
            for i, v in enumerate([10, 20, 30, 40, 50])
        ]
        _, b = classify(evs)
        self.assertGreaterEqual(b.files_p80_tokens, 40.0)
        self.assertLessEqual(b.files_p80_tokens, 50.0)

    def test_recent_turns_window_limit_20(self) -> None:
        evs = [
            _ev(i, "user" if i % 2 == 0 else "assistant", "text", 5)
            for i in range(50)
        ]
        _, b = classify(evs)
        self.assertEqual(len(b.recent_turns_window), 20)
        # 取的是「最後 20 筆」
        self.assertEqual(b.recent_turns_window[-1].idx, 49)
        self.assertEqual(b.recent_turns_window[0].idx, 30)

    def test_recent_turns_excludes_tool_events(self) -> None:
        evs = [
            _ev(0, "user", "text", 10),
            _ev(1, "assistant", "tool_use", 10, tool_name="Bash"),
            _ev(2, "tool", "tool_result", 10, tool_name="Bash"),
            _ev(3, "assistant", "text", 10),
        ]
        _, b = classify(evs)
        idx_list = [e.idx for e in b.recent_turns_window]
        self.assertIn(0, idx_list)
        self.assertIn(3, idx_list)
        self.assertNotIn(1, idx_list)
        self.assertNotIn(2, idx_list)


class TestClassifierWithFixture(unittest.TestCase):
    """用真實 fixture jsonl 跑一次，確認不崩 + 五類合計一致。"""

    def test_light_fixture_classifies(self) -> None:
        path = HERE / "fixtures" / "session_light.jsonl"
        if not path.exists():
            self.skipTest(f"fixture missing: {path}")
        events = scan(path)
        self.assertGreater(len(events), 0)
        tokens, baseline = classify(events)
        total = (tokens.conversation + tokens.files + tokens.tools_schema
                 + tokens.tools_runtime + tokens.system)
        self.assertEqual(total, sum(e.tokens_approx for e in events))
        self.assertEqual(baseline.total_tokens, total)
        self.assertIsInstance(baseline, SessionBaseline)


if __name__ == "__main__":
    unittest.main()
