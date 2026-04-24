"""規則註冊中心與 Rule Protocol / Finding DTO。

每條規則實作 Rule protocol：`name: str` + `apply(events, tokens, baseline) -> list[Finding]`，
並在模組載入時 `register(MyRule())` 完成註冊。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event


@dataclass(frozen=True)
class Finding:
    """單一浪費訊號。

    root_cause_key 用於 Layer 3 根因去重：同 key 取 severity 最高者。
    """

    type: str
    severity: str             # 'low' / 'medium' / 'high'
    root_cause_key: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Rule(Protocol):
    """規則介面。實作者必須是 pure function，不寫 DB/不寫檔。"""

    name: str

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        ...


_RULES: list[Rule] = []


def register(rule: Rule) -> None:
    """將 rule 加入註冊表；若同名已存在則替換（方便測試重載）。"""
    for i, r in enumerate(_RULES):
        if getattr(r, "name", "") == getattr(rule, "name", ""):
            _RULES[i] = rule
            return
    _RULES.append(rule)


def all_rules() -> list[Rule]:
    """回傳已註冊的規則（依註冊順序）。"""
    return list(_RULES)


def clear_rules() -> None:
    """測試用：清空註冊表。"""
    _RULES.clear()
