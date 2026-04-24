"""五類 token 歸類與 session-level baseline 計算。

五類：conversation / files / tools_schema / tools_runtime / system。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Iterable

from .scanner import Event


@dataclass(frozen=True)
class TokenBreakdown:
    """五類 token 合計（spec §6.3）。"""

    conversation: int = 0
    files: int = 0
    tools_schema: int = 0
    tools_runtime: int = 0
    system: int = 0


@dataclass(frozen=True)
class SessionBaseline:
    """Layer 2 自校基準：提供 rule 判斷『本 session 內算多嗎』。"""

    files_avg_tokens: float = 0.0
    files_p80_tokens: float = 0.0
    total_tokens: int = 0
    recent_turns_window: tuple[Event, ...] = field(default_factory=tuple)


# tool_name 歸類對照表
_RUNTIME_BUILTIN = {"bash", "webfetch", "websearch"}
_FILES_BUILTIN = {"read", "grep", "glob"}


def _is_tool_schema_event(e: Event) -> bool:
    """tool schema 事件的辨識。

    Claude Code JSONL 暫時沒有穩定欄位標示「session 啟動時載入的 tool schema」，
    先以下列啟發式：role=system 且 meta 含 'schema' 或 'tools' key，才歸 schema；
    否則視為無法偵測，先回 False（後續累計為 0）。
    """
    if e.role != "system":
        return False
    meta = e.meta or {}
    return bool(meta.get("is_tool_schema") or meta.get("tool_schema"))


def _is_system_event(e: Event) -> bool:
    """系統提示：system prompt / CLAUDE.md / @import 展開。"""
    if e.role == "system" and not _is_tool_schema_event(e):
        return True
    return False


def _classify_one(e: Event) -> str:
    """回傳該 event 該計入的類別名稱。"""
    if _is_tool_schema_event(e):
        return "tools_schema"
    if _is_system_event(e):
        return "system"
    if e.kind == "file_read":
        return "files"
    if e.kind in ("tool_use", "tool_result"):
        tool_name = (e.meta or {}).get("tool_name", "").lower()
        if tool_name in _FILES_BUILTIN:
            return "files"
        return "tools_runtime"
    # text: user / assistant 對話
    if e.kind == "text":
        return "conversation"
    return "conversation"


def _percentile(values: list[int], p: float) -> float:
    """簡易百分位數：空陣列回 0，P 介於 0-1。"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    if lo == hi:
        return float(sorted_vals[lo])
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def classify(events: Iterable[Event]) -> tuple[TokenBreakdown, SessionBaseline]:
    """將 events 歸到五類並產出 baseline。"""
    evs = list(events)

    buckets = {
        "conversation": 0,
        "files": 0,
        "tools_schema": 0,
        "tools_runtime": 0,
        "system": 0,
    }
    file_tokens_per_event: list[int] = []

    for e in evs:
        cat = _classify_one(e)
        buckets[cat] += e.tokens_approx
        if cat == "files" and e.kind == "file_read":
            file_tokens_per_event.append(e.tokens_approx)

    breakdown = TokenBreakdown(
        conversation=buckets["conversation"],
        files=buckets["files"],
        tools_schema=buckets["tools_schema"],
        tools_runtime=buckets["tools_runtime"],
        system=buckets["system"],
    )

    files_avg = float(mean(file_tokens_per_event)) if file_tokens_per_event else 0.0
    files_p80 = _percentile(file_tokens_per_event, 0.8)
    total = sum(buckets.values())

    # 最近 20 輪 user/assistant text
    text_events = [e for e in evs if e.kind == "text" and e.role in ("user", "assistant")]
    recent = tuple(text_events[-20:])

    baseline = SessionBaseline(
        files_avg_tokens=files_avg,
        files_p80_tokens=files_p80,
        total_tokens=total,
        recent_turns_window=recent,
    )
    return breakdown, baseline
