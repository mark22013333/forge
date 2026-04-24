"""short_turns_noise：連續 ≥ 5 輪 user/assistant < 15 tokens 且占近 20 輪 50% 以上。

只在「發生在最近 20 輪內」才報，避免會話初期就噴警告。
"""
from __future__ import annotations

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event
from .registry import Finding, register


_SHORT_TOKEN = 15
_RUN_THRESHOLD = 5
_RATIO = 0.5


class ShortTurnsNoise:
    name = "short_turns_noise"

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        window = baseline.recent_turns_window
        if len(window) < _RUN_THRESHOLD:
            return []

        short_flags = [e.tokens_approx < _SHORT_TOKEN for e in window]

        # 找最大連續 True 段長度
        max_run = 0
        run = 0
        for flag in short_flags:
            run = run + 1 if flag else 0
            if run > max_run:
                max_run = run

        if max_run < _RUN_THRESHOLD:
            return []

        ratio = sum(1 for f in short_flags if f) / len(short_flags)
        if ratio < _RATIO:
            return []

        return [Finding(
            type=self.name,
            severity="low",
            root_cause_key="short_turns_noise",
            summary=(
                f"{sum(short_flags)}/{len(short_flags)} recent turns "
                f"are < {_SHORT_TOKEN} tokens (max run {max_run})"
            ),
            detail={
                "short_count": sum(short_flags),
                "window_size": len(short_flags),
                "max_consecutive_run": max_run,
                "ratio": round(ratio, 3),
            },
        )]


register(ShortTurnsNoise())
