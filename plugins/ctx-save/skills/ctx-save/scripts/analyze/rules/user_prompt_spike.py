"""user_prompt_spike：單筆 user prompt > 4k tokens 且占對話 10% 以上。

典型來自貼 log/stack trace，建議改 attachment；本規則僅補充不升級。
"""
from __future__ import annotations

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event
from .registry import Finding, register


_MIN_TOKENS = 4000
_MIN_RATIO = 0.10


class UserPromptSpike:
    name = "user_prompt_spike"

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        conv = max(tokens.conversation, 1)
        out: list[Finding] = []
        for e in events:
            if e.kind != "text" or e.role != "user":
                continue
            if e.tokens_approx < _MIN_TOKENS:
                continue
            if e.tokens_approx / conv < _MIN_RATIO:
                continue
            out.append(Finding(
                type=self.name,
                severity="low",
                root_cause_key=f"user_prompt_spike:{e.idx}",
                summary=(
                    f"User prompt #{e.idx} is ~{e.tokens_approx} tokens "
                    f"({e.tokens_approx / conv:.0%} of conversation)"
                ),
                detail={
                    "event_idx": e.idx,
                    "tokens": e.tokens_approx,
                    "ratio": round(e.tokens_approx / conv, 3),
                },
            ))
        return out


register(UserPromptSpike())
