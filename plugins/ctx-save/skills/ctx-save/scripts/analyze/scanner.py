"""將 Claude Code session JSONL 展開成結構化 Event 序列。

資料來源：~/.claude/projects/<encoded-cwd>/<session-id>.jsonl
每行一筆 JSON，逐行 streaming 解析，失敗 silent skip。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


# code-like 副檔名，估 token 用 chars/3.35，其餘視為 text 用 chars/4
_CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs",
    ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".swift",
    ".sh", ".bash", ".zsh", ".sql", ".yaml", ".yml", ".toml", ".json",
    ".xml", ".html", ".css", ".scss", ".lua", ".r", ".m", ".mm",
    ".gradle", ".proto",
}

_FILE_READ_TOOLS = {"Read"}


@dataclass(frozen=True)
class Event:
    """單一事件：一筆 transcript row 經過語意歸納後的最小單位。"""

    idx: int
    ts: datetime
    role: str            # user / assistant / system / tool
    kind: str            # 'text' / 'tool_use' / 'tool_result' / 'file_read'
    tokens_approx: int
    meta: dict = field(default_factory=dict)


def _parse_ts(raw: Any) -> datetime:
    """盡量寬鬆地解析 timestamp；失敗回 epoch。"""
    if not raw:
        return datetime.fromtimestamp(0)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw))
        except (OSError, ValueError, OverflowError):
            return datetime.fromtimestamp(0)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.fromtimestamp(0)
    return datetime.fromtimestamp(0)


def _is_code_like(path: str) -> bool:
    if not path:
        return False
    suffix = Path(path).suffix.lower()
    return suffix in _CODE_EXTS


def _approx_tokens(text: str, code_like: bool = False) -> int:
    """character-based 近似估算，不呼叫任何 tokenizer。"""
    if not text:
        return 0
    divisor = 3.35 if code_like else 4.0
    return max(1, int(len(text) / divisor))


def _extract_text(content: Any) -> str:
    """content 可能是 str 或 list[{type, text, ...}]；統一抽出純文字。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for seg in content:
            if isinstance(seg, dict):
                if seg.get("type") == "text" and isinstance(seg.get("text"), str):
                    parts.append(seg["text"])
                elif isinstance(seg.get("text"), str):
                    parts.append(seg["text"])
            elif isinstance(seg, str):
                parts.append(seg)
        return "\n".join(parts)
    return ""


def _dump_json(obj: Any) -> str:
    """將任意結構壓為字串以估 token。"""
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


def _classify_message(msg: dict) -> list[dict]:
    """將單筆 transcript row 拆成 0..N 筆「分段事件」。

    回傳 list[{kind, role, text_or_payload, meta}]，由 scan() 再轉為 Event。
    """
    role = msg.get("role") or msg.get("type") or ""
    content = msg.get("content")
    segs: list[dict] = []

    # list 型 content（Anthropic 訊息標準）
    if isinstance(content, list):
        for seg in content:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            if seg_type == "text":
                segs.append({
                    "kind": "text",
                    "role": role or "assistant",
                    "text": seg.get("text", ""),
                    "meta": {},
                })
            elif seg_type == "tool_use":
                tool_name = seg.get("name", "")
                tool_input = seg.get("input", {}) or {}
                file_path = ""
                if isinstance(tool_input, dict):
                    file_path = (
                        tool_input.get("file_path")
                        or tool_input.get("path")
                        or ""
                    )
                if tool_name in _FILE_READ_TOOLS:
                    kind = "file_read"
                else:
                    kind = "tool_use"
                segs.append({
                    "kind": kind,
                    "role": "assistant",
                    "text": _dump_json(tool_input),
                    "meta": {
                        "tool_name": tool_name,
                        "file_path": str(file_path),
                        "mcp_server": (
                            tool_name.split("__")[1]
                            if tool_name.startswith("mcp__") and "__" in tool_name[5:]
                            else ""
                        ),
                    },
                })
            elif seg_type == "tool_result":
                result_text = _extract_text(seg.get("content"))
                segs.append({
                    "kind": "tool_result",
                    "role": "tool",
                    "text": result_text,
                    "meta": {
                        "tool_use_id": seg.get("tool_use_id", ""),
                    },
                })
        return segs

    # 純 str content 或其他：一律當 text
    text = _extract_text(content)
    if text:
        segs.append({
            "kind": "text",
            "role": role or "user",
            "text": text,
            "meta": {},
        })
    return segs


def scan(transcript_path: Path) -> list[Event]:
    """讀 JSONL 轉為 Event 列表；檔案不存在或壞行 silent skip。"""
    path = Path(transcript_path)
    if not path.exists() or not path.is_file():
        return []

    events: list[Event] = []
    idx = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(row, dict):
                    continue

                ts = _parse_ts(
                    row.get("timestamp")
                    or row.get("ts")
                    or row.get("created_at")
                )

                # Claude Code transcript row 通常是 {"type":"...", "message":{...}}
                inner = row.get("message") if isinstance(row.get("message"), dict) else row
                segs = _classify_message(inner)

                for seg in segs:
                    meta = dict(seg.get("meta", {}))
                    text = seg.get("text", "") or ""
                    kind = seg["kind"]
                    if kind == "file_read":
                        code_like = _is_code_like(meta.get("file_path", ""))
                    else:
                        code_like = kind in {"tool_use", "tool_result"} and bool(
                            meta.get("tool_name", "").lower() in {"bash", "grep", "glob"}
                        )
                    tokens = _approx_tokens(text, code_like=code_like)
                    events.append(Event(
                        idx=idx,
                        ts=ts,
                        role=seg.get("role", ""),
                        kind=kind,
                        tokens_approx=tokens,
                        meta=meta,
                    ))
                    idx += 1
    except OSError:
        return events

    return events
