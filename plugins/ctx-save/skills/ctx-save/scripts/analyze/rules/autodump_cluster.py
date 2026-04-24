"""autodump_cluster：兩筆 auto-dump 間隔 < 10 分鐘 且 session 內 ≥ 3 筆。

事件來源優先順序：
 1. events 中 meta.category == 'auto-dump' 且 kind='autodump_write' 的紀錄
 2. events 中 meta.category == 'auto-dump' 的 tool_use（fallback）
 若兩者皆無：回空（無法安全判定），避免 false positive。
"""
from __future__ import annotations

from datetime import timedelta

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event
from .registry import Finding, register


_CLUSTER_GAP = timedelta(minutes=10)
_MIN_COUNT = 3


class AutodumpCluster:
    name = "autodump_cluster"

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        dumps = [
            e for e in events
            if (e.meta or {}).get("category") == "auto-dump"
        ]
        if len(dumps) < _MIN_COUNT:
            return []

        dumps_sorted = sorted(dumps, key=lambda e: e.ts)
        close_pairs = 0
        for prev, cur in zip(dumps_sorted, dumps_sorted[1:]):
            if (cur.ts - prev.ts) < _CLUSTER_GAP:
                close_pairs += 1

        if close_pairs < _MIN_COUNT - 1:
            return []

        session_id = (dumps_sorted[0].meta or {}).get("session_id") or "current"
        return [Finding(
            type=self.name,
            severity="medium",
            root_cause_key=f"autodump_cluster:{session_id}",
            summary=f"{len(dumps_sorted)} auto-dumps within < 10min intervals",
            detail={
                "count": len(dumps_sorted),
                "close_pairs": close_pairs,
                "first_ts": dumps_sorted[0].ts.isoformat(),
                "last_ts": dumps_sorted[-1].ts.isoformat(),
            },
        )]


register(AutodumpCluster())
