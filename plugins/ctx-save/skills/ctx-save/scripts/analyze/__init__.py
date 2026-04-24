"""ctx-save analyze：對話健康診斷 pipeline。

流程：scanner → classifier → rules(registry) → dedup → renderer。
所有 DTO（Event/TokenBreakdown/SessionBaseline/Finding/Report）immutable-first。
"""
from __future__ import annotations

from .classifier import SessionBaseline, TokenBreakdown, classify
from .dedup import dedup
from .renderer import (
    Report,
    compute_score,
    compute_suggest_focus,
    render_cli,
    render_json,
)
from .rules import Finding, Rule, all_rules, register  # noqa: F401 — 觸發規則註冊
from .scanner import Event, scan

__all__ = [
    "Event",
    "Finding",
    "Report",
    "Rule",
    "SessionBaseline",
    "TokenBreakdown",
    "all_rules",
    "classify",
    "compute_score",
    "compute_suggest_focus",
    "dedup",
    "register",
    "render_cli",
    "render_json",
    "scan",
]
