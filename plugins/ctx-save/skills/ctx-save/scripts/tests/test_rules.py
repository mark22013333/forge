"""test_rules.py — 六條規則 × 七情境測試。

情境對照（spec §6.4）：
 1. duplicate_read 觸發
 2. duplicate_read below threshold（反向）
 3. autodump_cluster 觸發
 4. claudemd_bloat 自校：超過檔案平均 3 倍且 > 8k
 5. short_turns_noise 只在最近 20 輪內觸發
 6. user_prompt_spike 觸發
 7. unused_mcp_server 觸發（schema ≥ 1k、0 invocation、處於前 20%）
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze.classifier import SessionBaseline, TokenBreakdown, classify  # noqa: E402
from analyze.rules.autodump_cluster import AutodumpCluster  # noqa: E402
from analyze.rules.claudemd_bloat import ClaudemdBloat  # noqa: E402
from analyze.rules.duplicate_read import DuplicateRead  # noqa: E402
from analyze.rules.short_turns_noise import ShortTurnsNoise  # noqa: E402
from analyze.rules.unused_mcp_server import UnusedMcpServer  # noqa: E402
from analyze.rules.user_prompt_spike import UserPromptSpike  # noqa: E402
from analyze.scanner import Event  # noqa: E402


_T0 = datetime(2026, 4, 21, 10, 0, 0)


def _ev(idx: int, role: str, kind: str, tokens: int, ts=None, **meta) -> Event:
    return Event(
        idx=idx,
        ts=ts or _T0 + timedelta(seconds=idx),
        role=role,
        kind=kind,
        tokens_approx=tokens,
        meta=meta,
    )


class TestDuplicateRead(unittest.TestCase):
    """情境 1 & 2：duplicate_read。"""

    def test_triggers_when_all_three_gates_pass(self) -> None:
        """≥4 次 & ≥2000 tokens & 占對話 5% → 觸發 medium。"""
        events = [
            _ev(0, "user", "text", 20000),  # 撐起 conversation baseline
            *[
                _ev(i + 1, "assistant", "file_read", 600,
                    tool_name="Read", file_path="/repo/Push.java")
                for i in range(5)
            ],
        ]
        tokens, baseline = classify(events)
        findings = DuplicateRead().apply(events, tokens, baseline)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.type, "duplicate_read")
        self.assertEqual(f.severity, "medium")
        self.assertEqual(f.root_cause_key, "duplicate_read:/repo/Push.java")
        self.assertEqual(f.detail["count"], 5)
        self.assertGreaterEqual(f.detail["estimated_tokens"], 2000)

    def test_below_count_threshold_silent(self) -> None:
        """count=3 < 4 → 不報。"""
        events = [
            _ev(0, "user", "text", 20000),
            *[
                _ev(i + 1, "assistant", "file_read", 1000,
                    tool_name="Read", file_path="/a.py")
                for i in range(3)
            ],
        ]
        tokens, baseline = classify(events)
        self.assertEqual(DuplicateRead().apply(events, tokens, baseline), [])

    def test_below_cost_threshold_silent(self) -> None:
        """count ≥ 4 但總 tokens < 2k → 不報。"""
        events = [
            _ev(0, "user", "text", 20000),
            *[
                _ev(i + 1, "assistant", "file_read", 300,
                    tool_name="Read", file_path="/a.py")
                for i in range(5)
            ],
        ]
        tokens, baseline = classify(events)
        self.assertEqual(DuplicateRead().apply(events, tokens, baseline), [])

    def test_below_ratio_threshold_silent(self) -> None:
        """累計 2100 tokens 但對話 100k → 占比 < 5% → 不報。"""
        events = [
            _ev(0, "user", "text", 100000),
            *[
                _ev(i + 1, "assistant", "file_read", 550,
                    tool_name="Read", file_path="/a.py")
                for i in range(4)
            ],
        ]
        tokens, baseline = classify(events)
        self.assertEqual(DuplicateRead().apply(events, tokens, baseline), [])


class TestAutodumpCluster(unittest.TestCase):
    """情境 3：autodump_cluster。"""

    def test_three_dumps_within_10min_triggers(self) -> None:
        events = [
            _ev(0, "assistant", "tool_use", 50,
                ts=_T0, category="auto-dump", session_id="s1"),
            _ev(1, "assistant", "tool_use", 50,
                ts=_T0 + timedelta(minutes=3), category="auto-dump",
                session_id="s1"),
            _ev(2, "assistant", "tool_use", 50,
                ts=_T0 + timedelta(minutes=7), category="auto-dump",
                session_id="s1"),
        ]
        tokens, baseline = classify(events)
        findings = AutodumpCluster().apply(events, tokens, baseline)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].type, "autodump_cluster")
        self.assertEqual(findings[0].detail["count"], 3)

    def test_dumps_more_than_10min_apart_silent(self) -> None:
        events = [
            _ev(0, "assistant", "tool_use", 50,
                ts=_T0, category="auto-dump", session_id="s1"),
            _ev(1, "assistant", "tool_use", 50,
                ts=_T0 + timedelta(minutes=15), category="auto-dump",
                session_id="s1"),
            _ev(2, "assistant", "tool_use", 50,
                ts=_T0 + timedelta(minutes=30), category="auto-dump",
                session_id="s1"),
        ]
        tokens, baseline = classify(events)
        self.assertEqual(
            AutodumpCluster().apply(events, tokens, baseline), [])

    def test_fewer_than_three_dumps_silent(self) -> None:
        events = [
            _ev(0, "assistant", "tool_use", 50,
                ts=_T0, category="auto-dump", session_id="s1"),
            _ev(1, "assistant", "tool_use", 50,
                ts=_T0 + timedelta(minutes=2), category="auto-dump",
                session_id="s1"),
        ]
        tokens, baseline = classify(events)
        self.assertEqual(
            AutodumpCluster().apply(events, tokens, baseline), [])


class TestClaudemdBloat(unittest.TestCase):
    """情境 4：claudemd_bloat 自校。"""

    def test_triggers_when_above_8k_and_3x_files_avg(self) -> None:
        events = [
            _ev(0, "system", "text", 10000,
                source="claudemd", is_claudemd=True,
                file_path="/u/CLAUDE.md"),
            _ev(1, "assistant", "file_read", 500,
                tool_name="Read", file_path="/a.py"),
            _ev(2, "assistant", "file_read", 400,
                tool_name="Read", file_path="/b.py"),
        ]
        tokens, baseline = classify(events)
        findings = ClaudemdBloat().apply(events, tokens, baseline)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "medium")
        self.assertEqual(findings[0].root_cause_key, "claudemd_bloat")

    def test_below_8k_silent_even_if_ratio_passes(self) -> None:
        events = [
            _ev(0, "system", "text", 5000,
                source="claudemd", is_claudemd=True,
                file_path="/u/CLAUDE.md"),
            _ev(1, "assistant", "file_read", 100,
                tool_name="Read", file_path="/a.py"),
        ]
        tokens, baseline = classify(events)
        self.assertEqual(ClaudemdBloat().apply(events, tokens, baseline), [])

    def test_downgrades_severity_when_read_5_plus_times(self) -> None:
        """讀 CLAUDE.md ≥ 5 次時 severity 從 medium 降為 low。"""
        events = [
            _ev(0, "system", "text", 10000,
                source="claudemd", is_claudemd=True,
                file_path="/u/CLAUDE.md"),
            *[
                _ev(i + 1, "assistant", "file_read", 100,
                    tool_name="Read", file_path="/u/CLAUDE.md",
                    is_claudemd=True)
                for i in range(5)
            ],
            _ev(10, "assistant", "file_read", 500,
                tool_name="Read", file_path="/z.py"),
        ]
        tokens, baseline = classify(events)
        findings = ClaudemdBloat().apply(events, tokens, baseline)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "low")


class TestShortTurnsNoise(unittest.TestCase):
    """情境 5：short_turns_noise 只在最近 20 輪窗口觸發。"""

    def test_triggers_when_recent_window_is_mostly_short(self) -> None:
        events = [
            _ev(i, "user" if i % 2 == 0 else "assistant", "text", 5)
            for i in range(10)
        ]
        tokens, baseline = classify(events)
        findings = ShortTurnsNoise().apply(events, tokens, baseline)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].type, "short_turns_noise")
        self.assertEqual(findings[0].severity, "low")

    def test_long_recent_turns_silent(self) -> None:
        """最近 20 輪都是長訊息 → 不報。"""
        events = [
            _ev(i, "user" if i % 2 == 0 else "assistant", "text", 200)
            for i in range(10)
        ]
        tokens, baseline = classify(events)
        self.assertEqual(ShortTurnsNoise().apply(events, tokens, baseline), [])

    def test_old_short_turns_ignored_when_window_is_long(self) -> None:
        """老的短訊息被 window 擠出就不該報。"""
        events = [
            *[
                _ev(i, "user" if i % 2 == 0 else "assistant", "text", 2)
                for i in range(5)
            ],
            *[
                _ev(i + 5, "user" if i % 2 == 0 else "assistant", "text", 200)
                for i in range(20)
            ],
        ]
        tokens, baseline = classify(events)
        self.assertEqual(ShortTurnsNoise().apply(events, tokens, baseline), [])


class TestUserPromptSpike(unittest.TestCase):
    """情境 6：user_prompt_spike。"""

    def test_triggers_when_prompt_exceeds_4k_and_10pct(self) -> None:
        events = [
            _ev(0, "user", "text", 5000),
            _ev(1, "assistant", "text", 1000),
        ]
        tokens, baseline = classify(events)
        findings = UserPromptSpike().apply(events, tokens, baseline)
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.type, "user_prompt_spike")
        self.assertEqual(f.severity, "low")
        self.assertEqual(f.detail["event_idx"], 0)

    def test_below_4k_silent(self) -> None:
        events = [
            _ev(0, "user", "text", 3500),
            _ev(1, "assistant", "text", 1000),
        ]
        tokens, baseline = classify(events)
        self.assertEqual(UserPromptSpike().apply(events, tokens, baseline), [])

    def test_below_ratio_silent(self) -> None:
        """單筆 4500 tokens 但對話 100k → 占比 4.5% < 10%。"""
        events = [
            _ev(0, "user", "text", 4500),
            _ev(1, "assistant", "text", 95500),
        ]
        tokens, baseline = classify(events)
        self.assertEqual(UserPromptSpike().apply(events, tokens, baseline), [])


class TestUnusedMcpServer(unittest.TestCase):
    """情境 7：unused_mcp_server。"""

    def test_triggers_with_large_schema_and_no_invocation(self) -> None:
        events = [
            # 大 schema：2000 tokens，0 次呼叫
            _ev(0, "system", "text", 2000,
                is_tool_schema=True, mcp_server="linear",
                tool_name="mcp__linear__search"),
            # 小 schema：100 tokens，0 呼叫 — 不該被報（Layer 2 自校）
            _ev(1, "system", "text", 100,
                is_tool_schema=True, mcp_server="tiny",
                tool_name="mcp__tiny__ping"),
            # 另一個大 schema 但有使用 — 不該報
            _ev(2, "system", "text", 1500,
                is_tool_schema=True, mcp_server="active",
                tool_name="mcp__active__do"),
            _ev(3, "assistant", "tool_use", 30,
                tool_name="mcp__active__do", mcp_server="active"),
        ]
        tokens, baseline = classify(events)
        findings = UnusedMcpServer().apply(events, tokens, baseline)
        names = {f.detail["server"] for f in findings}
        self.assertIn("linear", names)
        self.assertNotIn("active", names)
        self.assertNotIn("tiny", names)

    def test_schema_below_1k_silent(self) -> None:
        events = [
            _ev(0, "system", "text", 500,
                is_tool_schema=True, mcp_server="small",
                tool_name="mcp__small__x"),
        ]
        tokens, baseline = classify(events)
        self.assertEqual(UnusedMcpServer().apply(events, tokens, baseline), [])


if __name__ == "__main__":
    unittest.main()
