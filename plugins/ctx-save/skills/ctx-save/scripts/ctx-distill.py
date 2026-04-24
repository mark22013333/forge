#!/usr/bin/env python3
"""ctx-distill.py — auto-dump 瘦身器（Heuristic summarizer，零 LLM / 零 pip）

用法:
    ctx-distill.py <save_id>                 distill 指定 save，寫入新筆（distilled_from_id=source_id）
    ctx-distill.py <save_id> --dry-run       只印預估壓縮比，不寫入
    ctx-distill.py --latest                  distill 最新一筆 auto-dump
    ctx-distill.py --latest --dry-run

壓縮規則（arch.md §7.1）：
    1. 移除重複出現的 tool_result（保留第一次與最後一次）
    2. 移除長度 < 20 tokens 的 user/assistant exchange（以空白切詞近似計 tokens）
    3. tool_use：保留 input summary；result body 截前 200 bytes
    4. 保留含 'error' / 'fail' / 'exception' / 'traceback' 關鍵字的訊息
    5. 訊息區段間插入 `[N messages elided]` 佔位

完成後回報：原長度 / 壓縮後長度 / 壓縮比 / 新筆 id。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------------

# 錯誤關鍵字（Layer 4 規則：必定保留）
ERROR_KEYWORDS = ("error", "fail", "exception", "traceback")

# 短交換門檻（以空白切詞近似 tokens；20 tokens 約 15-20 字）
SHORT_TURN_TOKEN_THRESHOLD = 20

# tool_result body 截斷長度
TOOL_RESULT_TRUNCATE_BYTES = 200

# 重複 tool_result 判定：同 signature 超過 2 次即視為重複
DUPLICATE_TOOL_RESULT_THRESHOLD = 2


# ---------------------------------------------------------------------------
# ctx-db 載入（檔名含 dash 必走 importlib，對齊 ctx-viewer 的 _load_ctx_db）
# ---------------------------------------------------------------------------


def _load_ctx_db():
    """載入同目錄的 ctx-db.py 模組。"""
    here = Path(__file__).resolve().parent
    ctx_db_path = here / "ctx-db.py"
    spec = importlib.util.spec_from_file_location("ctx_db", ctx_db_path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load ctx-db from " + str(ctx_db_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Heuristic 壓縮核心（pure function，純字串處理；便於 unittest）
# ---------------------------------------------------------------------------


def _approx_tokens(text: str) -> int:
    """以空白切詞近似 token 數；不呼叫任何 tokenizer。"""
    if not text:
        return 0
    return len(text.split())


def _has_error_keyword(text: str) -> bool:
    """判斷訊息是否含錯誤關鍵字（case-insensitive）。"""
    if not text:
        return False
    low = text.lower()
    return any(kw in low for kw in ERROR_KEYWORDS)


def _signature(text: str) -> str:
    """產生 tool_result 的重複判定 signature（前 120 字 + 長度）。"""
    head = text.strip()[:120]
    return str(len(text)) + "|" + head


def _parse_segments(raw: str) -> list[dict]:
    """將 transcript 切成結構化 segment 序列。

    辨識的起手式（行首匹配）：
      - `[user]` / `[assistant]` / `[system]` / `[tool]`
      - `User:` / `Assistant:` / `System:` / `Tool:`
      - `### tool_use(<name>)` / `### tool_result`

    無法辨識時整段歸為 `{'kind': 'text', 'role': 'unknown'}`。
    """
    if not raw:
        return []

    # 以行為單位切 segment；每個 segment 遇到新 header 就換。
    header_re = re.compile(
        r"^\s*(?:"
        r"\[(?P<bracket>user|assistant|system|tool|tool_use|tool_result)\]"
        r"|(?P<colon>User|Assistant|System|Tool):"
        r"|###\s*(?P<hash>tool_use|tool_result|user|assistant|system)(?:\((?P<name>[^)]*)\))?"
        r")",
        re.IGNORECASE,
    )

    lines = raw.splitlines()
    segments: list[dict] = []
    current = {
        "kind": "text",
        "role": "unknown",
        "tool_name": "",
        "lines": [],
    }

    def _flush():
        if current["lines"] or current["role"] != "unknown":
            text = "\n".join(current["lines"]).strip("\n")
            segments.append({
                "kind": current["kind"],
                "role": current["role"],
                "tool_name": current["tool_name"],
                "text": text,
            })

    for line in lines:
        m = header_re.match(line)
        if m:
            _flush()
            token = (
                m.group("bracket") or m.group("colon") or m.group("hash") or ""
            ).lower()
            tool_name = m.group("name") or ""
            if token in {"tool_use"}:
                kind, role = "tool_use", "assistant"
            elif token in {"tool_result", "tool"}:
                kind, role = "tool_result", "tool"
            elif token == "user":
                kind, role = "text", "user"
            elif token == "assistant":
                kind, role = "text", "assistant"
            elif token == "system":
                kind, role = "text", "system"
            else:
                kind, role = "text", token or "unknown"
            current = {
                "kind": kind,
                "role": role,
                "tool_name": tool_name,
                "lines": [],
            }
        else:
            current["lines"].append(line)
    _flush()

    # 過濾只剩空字串的 segment（除非是已標記的 role）
    return [s for s in segments if s["text"] or s["role"] != "unknown"]


def _compress_segments(segments: list[dict]) -> list[dict]:
    """規則式壓縮：規則 1-4；回傳壓縮後 segment 列表。

    規則 5（插入 [N messages elided]）由 _render 階段處理，
    這裡只負責『保留 / 丟棄 / 截斷』。
    """
    if not segments:
        return []

    # ---- 規則 1：同 signature tool_result 超過 2 次，只保留第一次與最後一次 ----
    sig_indexes: dict[str, list[int]] = {}
    for i, seg in enumerate(segments):
        if seg["kind"] != "tool_result":
            continue
        sig_indexes.setdefault(_signature(seg["text"]), []).append(i)

    drop_flags = [False] * len(segments)
    for indexes in sig_indexes.values():
        if len(indexes) > DUPLICATE_TOOL_RESULT_THRESHOLD:
            # 保留第一個與最後一個，其餘標記移除
            keep = {indexes[0], indexes[-1]}
            for idx in indexes:
                if idx in keep:
                    continue
                # 規則 4：含錯誤關鍵字者強制保留
                if _has_error_keyword(segments[idx]["text"]):
                    continue
                drop_flags[idx] = True

    # ---- 規則 2-4：逐筆判斷 + 截斷 ----
    out: list[dict] = []
    for i, seg in enumerate(segments):
        if drop_flags[i]:
            out.append({"__elided__": True})
            continue

        text = seg["text"]

        # 規則 4 優先：含錯誤關鍵字 → 全文保留
        if _has_error_keyword(text):
            out.append(dict(seg))
            continue

        # 規則 2：短的 user / assistant 交換 → 丟棄
        if seg["kind"] == "text" and seg["role"] in {"user", "assistant"}:
            if _approx_tokens(text) < SHORT_TURN_TOKEN_THRESHOLD:
                out.append({"__elided__": True})
                continue

        # 規則 3：tool_result body 截前 200 bytes
        if seg["kind"] == "tool_result":
            encoded = text.encode("utf-8", errors="replace")
            if len(encoded) > TOOL_RESULT_TRUNCATE_BYTES:
                truncated = (
                    encoded[:TOOL_RESULT_TRUNCATE_BYTES]
                    .decode("utf-8", errors="replace")
                )
                new_seg = dict(seg)
                new_seg["text"] = truncated + "…[truncated]"
                out.append(new_seg)
                continue

        # 規則 3：tool_use 保留 input summary（若 input 是 JSON，濃縮為 key 列表）
        if seg["kind"] == "tool_use":
            summary = _summarize_tool_input(text)
            new_seg = dict(seg)
            new_seg["text"] = summary
            out.append(new_seg)
            continue

        out.append(dict(seg))

    return out


def _summarize_tool_input(raw: str) -> str:
    """tool_use 的 input 區塊：若可 parse 為 JSON 就列 key 摘要，否則截前 200 bytes。"""
    text = raw.strip()
    if not text:
        return ""
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        encoded = text.encode("utf-8", errors="replace")
        if len(encoded) > TOOL_RESULT_TRUNCATE_BYTES:
            return (
                encoded[:TOOL_RESULT_TRUNCATE_BYTES]
                .decode("utf-8", errors="replace")
            ) + "…[truncated]"
        return text
    if isinstance(obj, dict):
        keys = list(obj.keys())
        # 挑幾個常用欄位的值作提示
        hints = []
        for hk in ("file_path", "path", "pattern", "command"):
            if hk in obj and isinstance(obj[hk], str):
                val = obj[hk][:80]
                hints.append(hk + "=" + val)
                if len(hints) >= 2:
                    break
        summary = "input_keys=" + ",".join(keys)
        if hints:
            summary += " | " + " ; ".join(hints)
        return summary
    return text[:200]


def _render(compressed: list[dict]) -> str:
    """將壓縮後 segments 渲染回可讀文字；連續 __elided__ 合併成一行佔位。"""
    lines: list[str] = []
    elided_run = 0
    for seg in compressed:
        if seg.get("__elided__"):
            elided_run += 1
            continue
        if elided_run > 0:
            lines.append("[" + str(elided_run) + " messages elided]")
            elided_run = 0
        header = _format_header(seg)
        if header:
            lines.append(header)
        if seg.get("text"):
            lines.append(seg["text"])
        lines.append("")
    if elided_run > 0:
        lines.append("[" + str(elided_run) + " messages elided]")
    # 結尾的多餘空行拿掉
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _format_header(seg: dict) -> str:
    kind = seg.get("kind", "")
    role = seg.get("role", "")
    if kind == "tool_use":
        name = seg.get("tool_name") or "tool"
        return "### tool_use(" + name + ")"
    if kind == "tool_result":
        return "### tool_result"
    if role and role != "unknown":
        return "[" + role + "]"
    return ""


def distill(content: str) -> tuple[str, dict]:
    """主入口：給原始 transcript 字串，回 (distilled_text, stats)。

    stats 結構：
        {
            "original_bytes": int,
            "distilled_bytes": int,
            "ratio": float,                  # 1 - distilled/original
            "original_segments": int,
            "distilled_segments": int,       # 不含 elided 佔位
            "elided_segments": int,
        }
    """
    segments = _parse_segments(content)
    compressed = _compress_segments(segments)
    rendered = _render(compressed)

    elided_count = sum(1 for s in compressed if s.get("__elided__"))
    kept_count = len(compressed) - elided_count

    original_bytes = len(content.encode("utf-8"))
    distilled_bytes = len(rendered.encode("utf-8"))
    ratio = 0.0
    if original_bytes > 0:
        ratio = 1.0 - (distilled_bytes / original_bytes)

    stats = {
        "original_bytes": original_bytes,
        "distilled_bytes": distilled_bytes,
        "ratio": round(ratio, 4),
        "original_segments": len(segments),
        "distilled_segments": kept_count,
        "elided_segments": elided_count,
    }
    return rendered, stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_source_id(
    ctx_db,
    db_path: Path,
    save_id: int | None,
    latest: bool,
) -> int | None:
    """決定要 distill 的 source id。--latest 時取最新一筆 auto-dump。"""
    if save_id is not None:
        return save_id
    if not latest:
        return None
    # 透過 ctx-db 的 list 介面查最新 auto-dump
    # 為了獨立於 list_sessions 的印出行為，這裡直接 sqlite 查
    import sqlite3
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT id FROM context_saves "
            "WHERE category = 'auto-dump' AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return int(row[0])


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ctx-distill.py",
        description="Heuristic summarizer for ctx-save auto-dump snapshots.",
    )
    parser.add_argument(
        "save_id",
        nargs="?",
        type=int,
        default=None,
        help="要 distill 的 context_saves.id",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="取最新一筆 auto-dump",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只印預估壓縮比不寫入",
    )
    parser.add_argument(
        "--save",
        dest="save",
        action="store_true",
        default=True,
        help="（預設）寫入新筆 distilled",
    )
    parser.add_argument(
        "--no-save",
        dest="save",
        action="store_false",
        help="等同 --dry-run：不寫入",
    )
    return parser.parse_args(argv)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None, ctx_db=None) -> int:
    """可被 unittest 以 ctx_db 注入後直接呼叫；CLI 跑時 ctx_db=None 走 _load_ctx_db。"""
    args = _parse_args(argv or sys.argv[1:])

    if args.save_id is None and not args.latest:
        print(
            "Usage: ctx-distill.py <save_id> [--dry-run] "
            "| ctx-distill.py --latest [--dry-run]",
            file=sys.stderr,
        )
        return 2

    if ctx_db is None:
        ctx_db = _load_ctx_db()

    db_path = Path(ctx_db.get_db_path())
    source_id = _resolve_source_id(ctx_db, db_path, args.save_id, args.latest)
    if source_id is None:
        _print_json({
            "status": "error",
            "error": "找不到可 distill 的 save（DB 為空或 id 不存在）",
        })
        return 1

    src = ctx_db.get_save(db_path, source_id) if hasattr(ctx_db, "get_save") else _fallback_get_save(db_path, source_id)
    if src is None:
        _print_json({
            "status": "error",
            "error": "source save 不存在或已軟刪：id=" + str(source_id),
        })
        return 1

    content = src.get("content") or ""
    distilled_text, stats = distill(content)

    if args.dry_run or not args.save:
        _print_json({
            "status": "dry-run",
            "source_id": source_id,
            "stats": stats,
        })
        return 0

    title = (src.get("title") or "auto-dump") + " [distilled]"
    new_id = ctx_db.save_distilled(db_path, source_id, title, distilled_text)
    _print_json({
        "status": "ok",
        "source_id": source_id,
        "new_id": new_id,
        "stats": stats,
    })
    return 0


def _fallback_get_save(db_path: Path, save_id: int) -> dict | None:
    """ctx-db 尚未有 get_save 時的回退查詢（測試環境相容）。"""
    import sqlite3
    if not db_path.exists():
        return None
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, session_id, project_path, title, category, content "
            "FROM context_saves "
            "WHERE id = ? AND deleted_at IS NULL",
            (int(save_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return dict(row)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 最外層兜底：不要讓 distill 撞進 Claude Code 流程
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        sys.exit(1)
