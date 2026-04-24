"""test_renderer.py — Layer 4 預算 + score + focus hint。

驗：
 - compute_score：low/medium/high 三等級的扣分正確，下限 0
 - compute_suggest_focus：只從 duplicate_read 抽 file，最多 3 個
 - render_cli：預設 budget top 5、show_all 解除
 - render_json：全量輸出，suppressed_by_budget 記錄 CLI 隱藏幾條
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

from analyze.classifier import TokenBreakdown  # noqa: E402
from analyze.renderer import (  # noqa: E402
    Report,
    compute_score,
    compute_suggest_focus,
    render_cli,
    render_json,
)
from analyze.rules.registry import Finding  # noqa: E402


def _f(type_: str, severity: str, summary: str = "", **detail) -> Finding:
    return Finding(
        type=type_,
        severity=severity,
        root_cause_key=f"{type_}:{summary or severity}",
        summary=summary or f"{type_}/{severity}",
        detail=detail,
    )


def _mk_report(findings: list[Finding], focus: str | None = None) -> Report:
    return Report(
        session_id="sess-test",
        ran_at=datetime(2026, 4, 21, 14, 30),
        score=compute_score(findings),
        tokens=TokenBreakdown(19000, 12000, 5000, 8000, 6000),
        findings=findings,
        suggest_focus=focus,
    )


class TestComputeScore(unittest.TestCase):
    """compute_score 三等級扣分。"""

    def test_empty_gives_100(self) -> None:
        self.assertEqual(compute_score([]), 100)

    def test_each_low_costs_3(self) -> None:
        self.assertEqual(compute_score([_f("x", "low")]), 97)

    def test_each_medium_costs_8(self) -> None:
        self.assertEqual(compute_score([_f("x", "medium")]), 92)

    def test_each_high_costs_15(self) -> None:
        self.assertEqual(compute_score([_f("x", "high")]), 85)

    def test_mixed_sum(self) -> None:
        findings = [
            _f("a", "high"), _f("b", "medium"), _f("c", "low"), _f("d", "low"),
        ]
        # 100 - 15 - 8 - 3 - 3
        self.assertEqual(compute_score(findings), 71)

    def test_score_floor_zero(self) -> None:
        findings = [_f("x", "high") for _ in range(20)]
        self.assertEqual(compute_score(findings), 0)


class TestComputeSuggestFocus(unittest.TestCase):
    """只從 duplicate_read 抽 file 欄位；最多 3 個。"""

    def test_none_when_no_duplicate_read(self) -> None:
        findings = [_f("unused_mcp_server", "low", server="linear")]
        self.assertIsNone(compute_suggest_focus(findings))

    def test_takes_file_from_duplicate_read(self) -> None:
        findings = [_f("duplicate_read", "medium", file="/a.py")]
        self.assertEqual(compute_suggest_focus(findings), "/a.py")

    def test_takes_at_most_three_files(self) -> None:
        findings = [
            _f("duplicate_read", "medium", file=f"/f{i}.py") for i in range(5)
        ]
        focus = compute_suggest_focus(findings)
        assert focus is not None
        self.assertEqual(focus.split(","), ["/f0.py", "/f1.py", "/f2.py"])

    def test_dedupes_same_file(self) -> None:
        findings = [
            _f("duplicate_read", "medium", file="/a.py"),
            _f("duplicate_read", "medium", file="/a.py"),
            _f("duplicate_read", "medium", file="/b.py"),
        ]
        self.assertEqual(compute_suggest_focus(findings), "/a.py,/b.py")

    def test_ignores_other_rule_types(self) -> None:
        findings = [
            _f("user_prompt_spike", "low", event_idx=3),
            _f("duplicate_read", "medium", file="/z.py"),
        ]
        self.assertEqual(compute_suggest_focus(findings), "/z.py")


class TestRenderCliBudget(unittest.TestCase):
    """Layer 4 預算：CLI 預設 top 5，超過附 `…and N more`。"""

    def test_default_shows_top_5_with_suppression_note(self) -> None:
        findings = [_f("rule", "low", summary=f"F{i}") for i in range(8)]
        out = render_cli(_mk_report(findings))
        self.assertIn("…and 3 more (use --all to see)", out)
        # 只能看到前 5 筆
        self.assertEqual(out.count("🟢 low"), 5)

    def test_show_all_bypasses_budget(self) -> None:
        findings = [_f("rule", "low", summary=f"F{i}") for i in range(8)]
        out = render_cli(_mk_report(findings), show_all=True)
        self.assertNotIn("more (use --all", out)
        self.assertEqual(out.count("🟢 low"), 8)

    def test_no_findings_shows_clean_message(self) -> None:
        out = render_cli(_mk_report([]))
        self.assertIn("(none — looks clean!)", out)

    def test_sort_by_severity_then_cost(self) -> None:
        """severity DESC → token cost DESC。"""
        findings = [
            _f("r1", "low", summary="LOW-A"),
            _f("r2", "high", summary="HIGH-A", estimated_tokens=100),
            _f("r3", "medium", summary="MED-B", estimated_tokens=3000),
            _f("r4", "medium", summary="MED-A", estimated_tokens=500),
        ]
        out = render_cli(_mk_report(findings))
        # HIGH-A 一定在 MED-B 之前；MED-B 在 MED-A 之前
        self.assertLess(out.index("HIGH-A"), out.index("MED-B"))
        self.assertLess(out.index("MED-B"), out.index("MED-A"))
        self.assertLess(out.index("MED-A"), out.index("LOW-A"))

    def test_suggest_focus_line_rendered(self) -> None:
        findings = [_f("duplicate_read", "medium", file="/a.py")]
        out = render_cli(_mk_report(findings, focus="/a.py"))
        self.assertIn("/compact focus on /a.py", out)

    def test_also_matched_rendered_as_sub_line(self) -> None:
        findings = [_f(
            "duplicate_read", "medium", summary="parent",
            also_matched=[{"type": "user_prompt_spike", "summary": "big prompt"}],
        )]
        out = render_cli(_mk_report(findings))
        self.assertIn("also:", out)
        self.assertIn("user_prompt_spike", out)


class TestRenderJsonFull(unittest.TestCase):
    """JSON 全量 + suppressed_by_budget。"""

    def test_json_has_required_keys(self) -> None:
        out = render_json(_mk_report([]))
        expected = {
            "session_id", "ran_at", "score", "context_percentage",
            "tokens", "findings", "suggest_focus", "suppressed_by_budget",
        }
        self.assertEqual(set(out.keys()), expected)

    def test_json_outputs_all_findings(self) -> None:
        findings = [_f("r", "low", summary=f"F{i}") for i in range(8)]
        out = render_json(_mk_report(findings))
        self.assertEqual(len(out["findings"]), 8)

    def test_suppressed_by_budget_reflects_cli_hiding(self) -> None:
        findings = [_f("r", "low", summary=f"F{i}") for i in range(9)]
        out = render_json(_mk_report(findings))
        self.assertEqual(out["suppressed_by_budget"], 4)  # 9 - 5

    def test_suppressed_zero_when_under_budget(self) -> None:
        findings = [_f("r", "medium", summary="ok")]
        out = render_json(_mk_report(findings))
        self.assertEqual(out["suppressed_by_budget"], 0)

    def test_tokens_serialized_as_dict(self) -> None:
        out = render_json(_mk_report([]))
        t = out["tokens"]
        self.assertIsInstance(t, dict)
        self.assertEqual(
            set(t.keys()),
            {"conversation", "files", "tools_schema", "tools_runtime",
             "system"},
        )

    def test_finding_dict_shape(self) -> None:
        findings = [_f("r", "low", summary="x", a=1)]
        out = render_json(_mk_report(findings))
        entry = out["findings"][0]
        self.assertEqual(
            set(entry.keys()),
            {"type", "severity", "root_cause_key", "summary", "detail"},
        )
        self.assertEqual(entry["detail"]["a"], 1)


if __name__ == "__main__":
    unittest.main()
