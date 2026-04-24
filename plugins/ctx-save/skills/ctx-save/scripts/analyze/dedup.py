"""Layer 3 根因去重。

同 root_cause_key 取 severity 最高者；被壓者加到勝者 detail['also_matched']。
另有特殊跨 root_cause_key 的 dedup 規則（spec §6.4）：
  - duplicate_read + autodump_cluster 同檔 → duplicate_read 勝
  - short_turns_noise + autodump_cluster → autodump_cluster 勝
  - user_prompt_spike + 同輪 duplicate_read → duplicate_read 勝
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .rules.registry import Finding


_SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def _rank(f: Finding) -> int:
    return _SEVERITY_ORDER.get(f.severity, 0)


def _annotate(winner: Finding, loser: Finding) -> Finding:
    """將 loser 追加到 winner.detail['also_matched']，回傳新的 Finding。"""
    detail = dict(winner.detail) if winner.detail else {}
    matched = list(detail.get("also_matched", []))
    matched.append({
        "type": loser.type,
        "severity": loser.severity,
        "root_cause_key": loser.root_cause_key,
        "summary": loser.summary,
    })
    detail["also_matched"] = matched
    return replace(winner, detail=detail)


def _dedup_by_root_cause(findings: list[Finding]) -> list[Finding]:
    """同 root_cause_key 取 severity 最高，被壓者加到勝者 also_matched。"""
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        groups.setdefault(f.root_cause_key, []).append(f)

    out: list[Finding] = []
    for key, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        original_winner = max(group, key=_rank)
        winner = original_winner
        for other in group:
            if other is original_winner:
                continue
            winner = _annotate(winner, other)
        out.append(winner)
    return out


def _cross_rule_dedup(findings: list[Finding]) -> list[Finding]:
    """特殊跨 root_cause_key 規則。回傳過濾後列表。"""
    by_type: dict[str, list[Finding]] = {}
    for f in findings:
        by_type.setdefault(f.type, []).append(f)

    suppressed_ids: set[int] = set()
    updated: dict[int, Finding] = {id(f): f for f in findings}

    # duplicate_read + autodump_cluster 同檔 → duplicate_read 勝
    dup_files = {
        f.detail.get("file"): f
        for f in by_type.get("duplicate_read", [])
        if f.detail.get("file")
    }
    for cluster in by_type.get("autodump_cluster", []):
        cluster_file = cluster.detail.get("file") if cluster.detail else None
        if cluster_file and cluster_file in dup_files:
            winner = dup_files[cluster_file]
            updated[id(winner)] = _annotate(updated[id(winner)], cluster)
            suppressed_ids.add(id(cluster))

    # short_turns_noise + autodump_cluster → autodump_cluster 勝
    if by_type.get("autodump_cluster") and by_type.get("short_turns_noise"):
        winner = by_type["autodump_cluster"][0]
        for stn in by_type["short_turns_noise"]:
            if id(stn) in suppressed_ids:
                continue
            updated[id(winner)] = _annotate(updated[id(winner)], stn)
            suppressed_ids.add(id(stn))

    # user_prompt_spike + 同輪 duplicate_read → duplicate_read 勝
    dup_idx_set: set[int] = set()
    for d in by_type.get("duplicate_read", []):
        for m in (d.detail.get("messages") or []):
            if isinstance(m, int):
                dup_idx_set.add(m)
    for spike in by_type.get("user_prompt_spike", []):
        spike_idx = spike.detail.get("event_idx")
        if isinstance(spike_idx, int) and spike_idx in dup_idx_set:
            winners = by_type.get("duplicate_read", [])
            if winners:
                w = winners[0]
                updated[id(w)] = _annotate(updated[id(w)], spike)
                suppressed_ids.add(id(spike))

    return [updated[id(f)] for f in findings if id(f) not in suppressed_ids]


def dedup(findings: Iterable[Finding]) -> list[Finding]:
    """執行 Layer 3 去重：先跨規則特例 → 再同 root_cause_key 合併。"""
    items = list(findings)
    if not items:
        return []
    step1 = _cross_rule_dedup(items)
    step2 = _dedup_by_root_cause(step1)
    return step2
