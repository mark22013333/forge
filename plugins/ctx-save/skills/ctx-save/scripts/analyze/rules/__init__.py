"""浪費偵測規則集合。

匯入各 rule 模組以觸發 `register(...)` side effect；
外部通常透過 `registry.all_rules()` 取得完整清單。
"""
from __future__ import annotations

from . import (
    autodump_cluster,
    claudemd_bloat,
    duplicate_read,
    short_turns_noise,
    unused_mcp_server,
    user_prompt_spike,
)
from .registry import Finding, Rule, all_rules, register

__all__ = [
    "Finding",
    "Rule",
    "all_rules",
    "register",
    "autodump_cluster",
    "claudemd_bloat",
    "duplicate_read",
    "short_turns_noise",
    "unused_mcp_server",
    "user_prompt_spike",
]
