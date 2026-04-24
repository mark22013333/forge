"""test_distill.py — ctx-distill 規則覆蓋與壓縮比驗證。

對應 arch.md §7.1 的五條 heuristic 規則。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from _loader import load_ctx_distill  # noqa: E402


FIXTURE = HERE / "fixtures" / "transcript_for_distill.txt"


class DistillRulesTest(unittest.TestCase):
    """針對每條規則各一個 test case。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ctx_distill = load_ctx_distill()
        cls.transcript = FIXTURE.read_text(encoding="utf-8")

    def test_removes_duplicate_tool_results(self) -> None:
        """重複 3 次以上的 tool_result，壓縮後應至少少掉 1 次出現。"""
        distilled, _ = self.ctx_distill.distill(self.transcript)

        # fixture 重複 tool_result（不含錯誤關鍵字）共 4 份 handler.py 內容；
        # 規則 1 應保留首末、丟中間兩份 → 壓縮後出現次數 < 原文。
        keyword = "SELECT * FROM users WHERE id=?"
        original_count = self.transcript.count(keyword)
        distilled_count = distilled.count(keyword)
        self.assertGreater(
            original_count, 1,
            msg="fixture 未包含足夠重複內容，測試前提無效",
        )
        self.assertLess(
            distilled_count, original_count,
            msg=f"重複 tool_result 未被去重：{distilled_count} vs {original_count}",
        )

    def test_keeps_error_messages(self) -> None:
        """含 error/traceback/exception 關鍵字的訊息必須保留。"""
        distilled, _ = self.ctx_distill.distill(self.transcript)
        # 規則 4：這些關鍵字在 fixture 錯誤訊息中出現，不可被規則 3 截掉
        for keyword in ("Traceback", "KeyError", "AssertionError", "FAILED"):
            self.assertIn(
                keyword, distilled,
                msg=f"錯誤關鍵字 {keyword!r} 被誤刪",
            )

    def test_compression_ratio_above_40pct(self) -> None:
        """典型 transcript 壓縮比應 ≥ 40%。"""
        _, stats = self.ctx_distill.distill(self.transcript)
        self.assertGreaterEqual(
            stats["ratio"], 0.40,
            msg=f"壓縮比僅 {stats['ratio']:.2%}，低於 40% 門檻；stats={stats}",
        )

    def test_short_turns_removed(self) -> None:
        """規則 2：短 user/assistant 交換（< 20 tokens）應被消掉。"""
        distilled, _ = self.ctx_distill.distill(self.transcript)
        # fixture 內 "[user]\nok\n\n[assistant]\n嗯。" 這些短互動
        # 不應完整保留（可能被合併成 elided 佔位）
        self.assertNotIn("\nok\n\n", distilled)

    def test_elided_placeholder_present(self) -> None:
        """規則 5：被濾掉的訊息會有 `[N messages elided]` 佔位。"""
        distilled, _ = self.ctx_distill.distill(self.transcript)
        self.assertRegex(distilled, r"\[\d+ messages? elided\]")

    def test_tool_result_truncated(self) -> None:
        """規則 3：超過 200 bytes 的 tool_result 會被截斷並加 [truncated] 標記。"""
        # 構造一個大 tool_result
        big_body = "x" * 400
        text = (
            "[assistant]\ncall\n\n"
            "### tool_result\n" + big_body + "\n\n"
            "[assistant]\nend\n"
        )
        distilled, _ = self.ctx_distill.distill(text)
        self.assertIn("[truncated]", distilled)
        # 壓縮後不可含原完整內容
        self.assertNotIn("x" * 400, distilled)


class DistillStatsTest(unittest.TestCase):
    """驗證 stats 結構欄位完整。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ctx_distill = load_ctx_distill()

    def test_stats_shape(self) -> None:
        text = "[user]\n" + "hello " * 50 + "\n[assistant]\n" + "world " * 50
        _, stats = self.ctx_distill.distill(text)
        for key in (
            "original_bytes", "distilled_bytes", "ratio",
            "original_segments", "distilled_segments", "elided_segments",
        ):
            self.assertIn(key, stats, msg=f"stats 缺欄位 {key}")
        self.assertGreater(stats["original_bytes"], 0)

    def test_empty_input(self) -> None:
        """空字串輸入不應爆；ratio=0、bytes=0。"""
        distilled, stats = self.ctx_distill.distill("")
        self.assertEqual(distilled, "")
        self.assertEqual(stats["original_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
