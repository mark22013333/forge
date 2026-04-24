#!/usr/bin/env python3
"""`/ctx-save analyze` CLI 入口。

用法：
    ctx-analyze.py [--session <id>] [--all] [--json] [--save/--no-save]

流程：scanner → classifier → rules(via registry) → dedup → renderer。
結果可透過 ctx-db Facade 寫入 context_diagnoses（動態 import）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SELF_DIR = Path(__file__).resolve().parent
if str(_SELF_DIR) not in sys.path:
    sys.path.insert(0, str(_SELF_DIR))

from analyze import (  # noqa: E402
    classify,
    compute_score,
    compute_suggest_focus,
    dedup,
    Report,
    render_cli,
    render_json,
    scan,
)
from analyze.rules import all_rules  # noqa: E402


# ---------------------------------------------------------------------------
# 輔助：定位 transcript 與 session id
# ---------------------------------------------------------------------------

def _encoded_cwd(cwd: Path) -> str:
    """Claude Code 的 encoded-cwd：把 '/' 換成 '-'。"""
    return str(cwd).replace("/", "-")


def _projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def _candidate_transcripts(session_id: str | None) -> list[Path]:
    """回傳可能的 transcript 路徑（依修改時間排序）。"""
    root = _projects_root()
    if not root.exists():
        return []
    cwd = Path.cwd()
    enc = _encoded_cwd(cwd)
    project_dir = root / enc

    candidates: list[Path] = []
    if session_id:
        # 嘗試在所有 project 目錄中找
        for pdir in root.glob("*"):
            if not pdir.is_dir():
                continue
            p = pdir / f"{session_id}.jsonl"
            if p.exists():
                candidates.append(p)
    else:
        # 當前專案目錄下最新的 jsonl
        if project_dir.exists():
            jsonls = sorted(
                project_dir.glob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(jsonls)
    return candidates


def _infer_session_id(transcript_path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    return transcript_path.stem or "unknown"


# ---------------------------------------------------------------------------
# 輔助：動態載入 ctx-db Facade（檔名帶破折號不能直接 import）
# ---------------------------------------------------------------------------

def _load_ctx_db():
    path = _SELF_DIR / "ctx-db.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("ctx_db_facade", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _save_to_db(report: Report, json_payload: dict[str, Any]) -> int | None:
    """呼叫 ctx-db.save_diagnosis。不存在或失敗時回 None。"""
    mod = _load_ctx_db()
    if mod is None or not hasattr(mod, "save_diagnosis"):
        return None
    try:
        db_path = mod.get_db_path() if hasattr(mod, "get_db_path") else None
    except Exception:
        db_path = None
    payload = {
        "session_id": report.session_id,
        "ran_at": report.ran_at.isoformat(),
        "context_percentage": report.context_percentage,
        "score": report.score,
        "tokens_conversation": report.tokens.conversation,
        "tokens_files": report.tokens.files,
        "tokens_tools_schema": report.tokens.tools_schema,
        "tokens_tools_runtime": report.tokens.tools_runtime,
        "tokens_system": report.tokens.system,
        "findings_json": json.dumps(json_payload.get("findings", []), ensure_ascii=False),
        "suggest_focus": report.suggest_focus,
    }
    try:
        return mod.save_diagnosis(db_path, payload)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(transcript_path: Path, session_id: str) -> Report:
    events = scan(transcript_path)
    tokens, baseline = classify(events)

    raw_findings = []
    for rule in all_rules():
        try:
            raw_findings.extend(rule.apply(events, tokens, baseline))
        except Exception:
            # 單一規則失敗不影響其他（符合 pure-function 原則）
            continue
    deduped = dedup(raw_findings)

    report = Report(
        session_id=session_id,
        ran_at=datetime.now(),
        score=compute_score(deduped),
        tokens=tokens,
        findings=deduped,
        suggest_focus=compute_suggest_focus(deduped),
        context_percentage=None,
    )
    return report


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ctx-analyze",
        description="對話健康診斷（/ctx-save analyze）",
    )
    p.add_argument("--session", dest="session", default=None,
                   help="指定 session id；預設使用當前專案最新 jsonl")
    p.add_argument("--all", dest="show_all", action="store_true",
                   help="解除 Layer 4 預算上限，顯示全部 findings")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="輸出 JSON（供 Web Viewer / scripting）")
    save_group = p.add_mutually_exclusive_group()
    save_group.add_argument("--save", dest="save", action="store_true",
                            help="寫入 context_diagnoses（預設行為）")
    save_group.add_argument("--no-save", dest="save", action="store_false",
                            help="不寫入 DB")
    p.set_defaults(save=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    candidates = _candidate_transcripts(args.session)
    if not candidates:
        hint = args.session or "<current cwd>"
        sys.stderr.write(
            f"⚠ 找不到 transcript（session={hint}）。\n"
            f"   檢查 ~/.claude/projects/ 是否有對應 jsonl。\n"
        )
        return 2

    transcript = candidates[0]
    session_id = _infer_session_id(transcript, args.session)
    report = _run_pipeline(transcript, session_id)
    json_payload = render_json(report)

    if args.save:
        _save_to_db(report, json_payload)

    if args.as_json:
        print(json.dumps(json_payload, ensure_ascii=False, indent=2))
    else:
        print(render_cli(report, show_all=args.show_all))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
