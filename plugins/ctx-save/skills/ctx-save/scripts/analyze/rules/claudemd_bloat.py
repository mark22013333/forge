"""claudemd_bloat：CLAUDE.md 相關系統提示 > 8k tokens 且 > files 平均 3 倍。

讀過 CLAUDE.md 的輪次 ≥ 5 時 severity 降一級（medium → low）。
"""
from __future__ import annotations

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event
from .registry import Finding, register


_MIN_TOKENS = 8000
_RATIO_TO_AVG = 3.0
_DOWNGRADE_THRESHOLD = 5


class ClaudemdBloat:
    name = "claudemd_bloat"

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        # 累計「CLAUDE.md 類」的 token：system 類事件中 meta 指到 CLAUDE.md
        # 或 file_read 對 CLAUDE.md 的讀取
        claudemd_tokens = 0
        read_rounds = 0
        for e in events:
            meta = e.meta or {}
            fpath = meta.get("file_path", "") or ""
            is_claudemd = fpath.endswith("CLAUDE.md") or meta.get("is_claudemd")
            if e.role == "system" and (is_claudemd or meta.get("source") == "claudemd"):
                claudemd_tokens += e.tokens_approx
            if e.kind == "file_read" and is_claudemd:
                read_rounds += 1
                claudemd_tokens += e.tokens_approx

        if claudemd_tokens <= _MIN_TOKENS:
            return []
        if baseline.files_avg_tokens <= 0:
            return []
        if claudemd_tokens < baseline.files_avg_tokens * _RATIO_TO_AVG:
            return []

        severity = "medium"
        if read_rounds >= _DOWNGRADE_THRESHOLD:
            severity = "low"

        return [Finding(
            type=self.name,
            severity=severity,
            root_cause_key="claudemd_bloat",
            summary=(
                f"CLAUDE.md consumes ~{claudemd_tokens} tokens "
                f"({claudemd_tokens / max(baseline.files_avg_tokens, 1):.1f}x files avg)"
            ),
            detail={
                "claudemd_tokens": claudemd_tokens,
                "files_avg_tokens": baseline.files_avg_tokens,
                "read_rounds": read_rounds,
            },
        )]


register(ClaudemdBloat())
