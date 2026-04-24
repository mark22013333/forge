"""Report DTO + Layer 4 預算 + CLI / JSON 輸出。

- `compute_score`：健康分 0-100。
- `compute_suggest_focus`：從 duplicate_read findings 取最多前 3 個檔名。
- `render_cli`：spec §6.6 格式；預設 top 5，超過附 `…and N more (use --all to see)`。
- `render_json`：全量 findings；加 `suppressed_by_budget` 紀錄 CLI 隱藏幾條。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .classifier import TokenBreakdown
from .rules.registry import Finding


_SEVERITY_PENALTY = {"low": 3, "medium": 8, "high": 15}
_SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}
_SEVERITY_ICON = {"high": "🔴 high  ", "medium": "🟡 medium", "low": "🟢 low   "}
_CLI_BUDGET = 5
_FOCUS_LIMIT = 3


@dataclass
class Report:
    """診斷結果總覽。"""

    session_id: str
    ran_at: datetime
    score: int
    tokens: TokenBreakdown
    findings: list[Finding] = field(default_factory=list)
    suggest_focus: str | None = None
    context_percentage: float | None = None


def compute_score(findings: list[Finding]) -> int:
    """健康分 = 100 - sum(severity_penalty)，下限 0。"""
    base = 100
    for f in findings:
        base -= _SEVERITY_PENALTY.get(f.severity, 0)
    return max(0, base)


def compute_suggest_focus(findings: list[Finding]) -> str | None:
    """從 duplicate_read 的 file 欄位取前 3 個，逗號串接。"""
    files: list[str] = []
    for f in findings:
        if f.type != "duplicate_read":
            continue
        fpath = (f.detail or {}).get("file")
        if isinstance(fpath, str) and fpath and fpath not in files:
            files.append(fpath)
        if len(files) >= _FOCUS_LIMIT:
            break
    if not files:
        return None
    return ",".join(files)


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """severity DESC → token cost DESC → 規則原始順序。"""
    def _cost(f: Finding) -> int:
        d = f.detail or {}
        for key in ("estimated_tokens", "schema_tokens", "tokens", "claudemd_tokens"):
            v = d.get(key)
            if isinstance(v, (int, float)):
                return int(v)
        return 0
    return sorted(
        enumerate(findings),
        key=lambda t: (-_SEVERITY_ORDER.get(t[1].severity, 0), -_cost(t[1]), t[0]),
    )


def _fmt_tokens_line(label: str, val: int, total: int) -> str:
    pct = (val / total * 100.0) if total > 0 else 0.0
    kb = val * 4 / 1024.0
    return f"  {label:<12} {pct:5.1f}%  ({kb:5.1f} KB ≈ {val:>5} tokens)"


def render_cli(report: Report, show_all: bool = False) -> str:
    """人類可讀輸出；套用 Layer 4 預算。"""
    t = report.tokens
    total = t.conversation + t.files + t.tools_schema + t.tools_runtime + t.system

    lines: list[str] = []
    lines.append("🩺 Context Health Report")
    lines.append(f"Session: {report.session_id}")
    lines.append(f"Score: {report.score} / 100")
    if report.context_percentage is not None:
        lines.append(f"Context used: {report.context_percentage:.1f}%")
    lines.append("")
    lines.append("Token breakdown:")
    lines.append(_fmt_tokens_line("對話", t.conversation, total))
    lines.append(_fmt_tokens_line("檔案讀取", t.files, total))
    lines.append(_fmt_tokens_line("工具 schema", t.tools_schema, total))
    lines.append(_fmt_tokens_line("工具 runtime", t.tools_runtime, total))
    lines.append(_fmt_tokens_line("系統提示", t.system, total))
    lines.append("")

    sorted_findings = [f for _, f in _sort_findings(report.findings)]
    visible = sorted_findings if show_all else sorted_findings[:_CLI_BUDGET]
    suppressed = 0 if show_all else max(0, len(sorted_findings) - _CLI_BUDGET)

    if visible:
        lines.append("Findings:")
        for f in visible:
            icon = _SEVERITY_ICON.get(f.severity, f.severity)
            lines.append(f"  {icon}  {f.summary}")
            also = (f.detail or {}).get("also_matched") or []
            for am in also:
                lines.append(f"             ↳ also: {am.get('type')} — {am.get('summary', '')}")
        if suppressed > 0:
            lines.append(f"  …and {suppressed} more (use --all to see)")
    else:
        lines.append("Findings: (none — looks clean!)")

    if report.suggest_focus:
        lines.append("")
        lines.append("Suggested /compact focus:")
        lines.append(f"  /compact focus on {report.suggest_focus}")

    return "\n".join(lines)


def _finding_to_dict(f: Finding) -> dict[str, Any]:
    return {
        "type": f.type,
        "severity": f.severity,
        "root_cause_key": f.root_cause_key,
        "summary": f.summary,
        "detail": dict(f.detail) if f.detail else {},
    }


def render_json(report: Report) -> dict[str, Any]:
    """全量輸出；加 suppressed_by_budget 紀錄 CLI 會隱藏幾條。"""
    sorted_findings = [f for _, f in _sort_findings(report.findings)]
    suppressed = max(0, len(sorted_findings) - _CLI_BUDGET)
    return {
        "session_id": report.session_id,
        "ran_at": report.ran_at.isoformat(),
        "score": report.score,
        "context_percentage": report.context_percentage,
        "tokens": asdict(report.tokens),
        "findings": [_finding_to_dict(f) for f in sorted_findings],
        "suggest_focus": report.suggest_focus,
        "suppressed_by_budget": suppressed,
    }
