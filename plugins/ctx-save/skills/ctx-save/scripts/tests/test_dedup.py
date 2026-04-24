"""test_dedup.py — Layer 3 根因去重（spec §6.4）。

驗證四組合：
 1. 同 root_cause_key 取 severity 最高、被壓者進入 also_matched
 2. duplicate_read + autodump_cluster 同檔 → duplicate_read 勝
 3. short_turns_noise + autodump_cluster → autodump_cluster 勝
 4. user_prompt_spike + 同輪 duplicate_read → duplicate_read 勝
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from analyze.dedup import dedup  # noqa: E402
from analyze.rules.registry import Finding  # noqa: E402


def _f(type_: str, severity: str, key: str, summary: str = "", **detail) -> Finding:
    return Finding(
        type=type_,
        severity=severity,
        root_cause_key=key,
        summary=summary or f"{type_}/{severity}",
        detail=detail,
    )


class TestSameRootCauseKeyMerging(unittest.TestCase):
    """同 root_cause_key 取 severity 最高者；被壓者寫入 also_matched。"""

    def test_empty_in_empty_out(self) -> None:
        self.assertEqual(dedup([]), [])

    def test_single_finding_passes_through(self) -> None:
        f = _f("duplicate_read", "medium", "duplicate_read:/a.py")
        out = dedup([f])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].type, "duplicate_read")
        self.assertNotIn("also_matched", out[0].detail)

    def test_same_key_keeps_highest_severity(self) -> None:
        low = _f("dup_custom", "low", "dup:X")
        high = _f("dup_custom", "high", "dup:X")
        mid = _f("dup_custom", "medium", "dup:X")
        out = dedup([low, high, mid])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].severity, "high")
        also = out[0].detail.get("also_matched", [])
        self.assertEqual(len(also), 2)
        self.assertEqual({a["severity"] for a in also}, {"low", "medium"})


class TestCrossRuleDedup(unittest.TestCase):
    """跨 root_cause_key 的特例（arch/spec 表格）。"""

    def test_duplicate_read_wins_over_same_file_autodump(self) -> None:
        """同檔觸發 duplicate_read + autodump_cluster → duplicate_read 勝。"""
        dup = _f(
            "duplicate_read", "medium", "duplicate_read:/repo/A.java",
            file="/repo/A.java", count=5, estimated_tokens=3000,
        )
        cluster = _f(
            "autodump_cluster", "medium", "autodump_cluster:sess-1",
            file="/repo/A.java", count=3,
        )
        out = dedup([dup, cluster])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].type, "duplicate_read")
        also = out[0].detail.get("also_matched", [])
        self.assertEqual(len(also), 1)
        self.assertEqual(also[0]["type"], "autodump_cluster")

    def test_autodump_cluster_wins_over_short_turns_noise(self) -> None:
        cluster = _f("autodump_cluster", "medium", "autodump_cluster:sess-1",
                     count=3)
        noise = _f("short_turns_noise", "low", "short_turns_noise",
                   short_count=8)
        out = dedup([cluster, noise])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].type, "autodump_cluster")
        also = out[0].detail.get("also_matched", [])
        self.assertEqual(len(also), 1)
        self.assertEqual(also[0]["type"], "short_turns_noise")

    def test_duplicate_read_wins_over_same_turn_user_prompt_spike(self) -> None:
        """spike.event_idx 位於 duplicate_read.messages 中 → duplicate_read 勝。"""
        dup = _f(
            "duplicate_read", "medium", "duplicate_read:/x.py",
            file="/x.py", count=4, estimated_tokens=2500,
            messages=[10, 45, 67, 99],
        )
        spike = _f(
            "user_prompt_spike", "low", "user_prompt_spike:45",
            event_idx=45, tokens=5000, ratio=0.2,
        )
        out = dedup([dup, spike])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].type, "duplicate_read")
        also = out[0].detail.get("also_matched", [])
        self.assertEqual(len(also), 1)
        self.assertEqual(also[0]["type"], "user_prompt_spike")

    def test_spike_on_unrelated_turn_coexists_with_duplicate_read(self) -> None:
        """spike 的 event_idx 不在 messages 裡 → 兩筆並存。"""
        dup = _f(
            "duplicate_read", "medium", "duplicate_read:/x.py",
            file="/x.py", count=4, estimated_tokens=2500,
            messages=[10, 20, 30, 40],
        )
        spike = _f(
            "user_prompt_spike", "low", "user_prompt_spike:99",
            event_idx=99, tokens=5000,
        )
        out = dedup([dup, spike])
        types = sorted(f.type for f in out)
        self.assertEqual(types, ["duplicate_read", "user_prompt_spike"])

    def test_claudemd_and_mcp_are_independent(self) -> None:
        """兩不同根因並列，互不壓抑。"""
        claudemd = _f("claudemd_bloat", "medium", "claudemd_bloat")
        mcp = _f("unused_mcp_server", "low", "unused_mcp:linear",
                 server="linear", schema_tokens=3000)
        out = dedup([claudemd, mcp])
        types = sorted(f.type for f in out)
        self.assertEqual(types, ["claudemd_bloat", "unused_mcp_server"])


class TestDedupImmutability(unittest.TestCase):
    """dedup 應產出新 Finding，不改舊物件。"""

    def test_winner_detail_is_new_object(self) -> None:
        a = _f("x", "low", "k")
        b = _f("x", "high", "k")
        out = dedup([a, b])
        # 原始 findings 不應被突變
        self.assertNotIn("also_matched", a.detail)
        self.assertNotIn("also_matched", b.detail)
        self.assertEqual(len(out), 1)
        self.assertIn("also_matched", out[0].detail)


if __name__ == "__main__":
    unittest.main()
