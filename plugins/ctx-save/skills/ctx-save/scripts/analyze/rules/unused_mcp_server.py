"""unused_mcp_server：某 MCP server 的 schema ≥ 1k tokens 且 session 內 0 invocation。

Layer 2 自校：只在該 MCP schema tokens 佔 tools_schema 前 20% 時才報
（即屬於「本 session 最胖的前 20%」，避免報一堆小 MCP）。
"""
from __future__ import annotations

from collections import defaultdict

from ..classifier import SessionBaseline, TokenBreakdown
from ..scanner import Event
from .registry import Finding, register


_MIN_SCHEMA_TOKENS = 1000


class UnusedMcpServer:
    name = "unused_mcp_server"

    def apply(
        self,
        events: list[Event],
        tokens: TokenBreakdown,
        baseline: SessionBaseline,
    ) -> list[Finding]:
        schema_tokens: dict[str, int] = defaultdict(int)
        invocations: dict[str, int] = defaultdict(int)

        for e in events:
            meta = e.meta or {}
            server = meta.get("mcp_server", "")
            tool_name = meta.get("tool_name", "")
            if not server and tool_name.startswith("mcp__"):
                parts = tool_name.split("__")
                server = parts[1] if len(parts) >= 2 else ""
            if not server:
                continue
            if meta.get("is_tool_schema") or meta.get("tool_schema"):
                schema_tokens[server] += e.tokens_approx
            elif e.kind == "tool_use":
                invocations[server] += 1

        candidates = [
            (s, t) for s, t in schema_tokens.items()
            if t >= _MIN_SCHEMA_TOKENS and invocations.get(s, 0) == 0
        ]
        if not candidates:
            return []

        all_sizes = sorted(schema_tokens.values(), reverse=True)
        cutoff_idx = max(1, len(all_sizes) // 5)  # 前 20%
        threshold = all_sizes[cutoff_idx - 1] if all_sizes else 0

        out: list[Finding] = []
        for server, stokens in candidates:
            if stokens < threshold:
                continue
            out.append(Finding(
                type=self.name,
                severity="low",
                root_cause_key=f"unused_mcp:{server}",
                summary=f"MCP server '{server}' schema ~{stokens} tokens, 0 invocations",
                detail={
                    "server": server,
                    "schema_tokens": stokens,
                    "invocations": 0,
                },
            ))
        return out


register(UnusedMcpServer())
