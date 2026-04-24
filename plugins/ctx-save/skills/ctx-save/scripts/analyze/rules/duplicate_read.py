"""duplicate_read：同檔 ≥ 4 次 且 累計 ≥ 2k tokens 且 占對話 5% 以上。"""
from __future__ import annotations

from collections import Counter, defaultdict

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event
from .registry import Finding, register


class DuplicateRead:
    name = "duplicate_read"

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        counts: Counter[str] = Counter()
        costs: dict[str, int] = defaultdict(int)
        msg_idx: dict[str, list[int]] = defaultdict(list)
        for e in events:
            if e.kind != "file_read":
                continue
            fpath = (e.meta or {}).get("file_path") or ""
            if not fpath:
                continue
            counts[fpath] += 1
            costs[fpath] += e.tokens_approx
            msg_idx[fpath].append(e.idx)

        conv = max(tokens.conversation, 1)
        out: list[Finding] = []
        for fpath, count in counts.items():
            cost = costs[fpath]
            if count < 4:
                continue
            if cost < 2000:
                continue
            if cost / conv < 0.05:
                continue
            out.append(Finding(
                type=self.name,
                severity="medium",
                root_cause_key=f"duplicate_read:{fpath}",
                summary=f"{fpath} read {count} times (~{cost} tokens)",
                detail={
                    "file": fpath,
                    "count": count,
                    "estimated_tokens": cost,
                    "messages": msg_idx[fpath],
                },
            ))
        return out


register(DuplicateRead())
