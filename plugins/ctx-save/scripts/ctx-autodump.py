#!/usr/bin/env python3
"""ctx-autodump.py — PreCompact hook：壓縮前 raw dump 到 SQLite

Claude Code PreCompact hook 透過 stdin 傳 JSON payload：
    {
      "session_id": "...",
      "transcript_path": "/abs/path.jsonl",
      "hook_event_name": "PreCompact",
      "trigger": "auto" | "manual",
      ...
    }

設計原則：
  - 無條件執行（不看 config.mode），這是手機離開場景的保底
  - 任何錯誤都靜默落地到 ~/.cache/ctx-save/autodump-error.log
  - 永遠 exit 0，不阻塞 compact
  - 讀 config 的 transcript_tail_kb 決定保留多少 raw 對話
"""

import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
CTX_DB_PY = PLUGIN_ROOT / "skills" / "ctx-save" / "scripts" / "ctx-db.py"
CTX_CONFIG_PY = SCRIPT_DIR / "ctx-config.py"

SESSION_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def log_error(msg: str) -> None:
    log_dir = Path.home() / ".cache" / "ctx-save"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "autodump-error.log").open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def load_config() -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["python3", str(CTX_CONFIG_PY), "get-all"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout.strip())
    except Exception as exc:
        log_error(f"load_config failed: {exc}")
    return {"transcript_tail_kb": 200}


def read_tail(path: Path, tail_bytes: int) -> str:
    try:
        fsize = path.stat().st_size
        start = max(0, fsize - tail_bytes)
        with path.open("rb") as f:
            f.seek(start)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        # 若非檔頭切入，捨棄第一行（半行）
        if start > 0:
            idx = text.find("\n")
            if idx >= 0:
                text = text[idx + 1:]
        return text
    except Exception as exc:
        return f"(讀取 transcript 失敗: {exc})"


def sanitize_session_id(raw: str) -> str:
    if raw and SESSION_ID_REGEX.match(raw):
        return raw
    return "autodump" + datetime.utcnow().strftime("%Y%m%d%H%M%S")


def build_record(payload: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    session_id = sanitize_session_id(str(payload.get("session_id") or ""))
    transcript_path = payload.get("transcript_path") or ""
    trigger = str(payload.get("trigger", "auto"))
    tail_kb = int(config.get("transcript_tail_kb", 200) or 200)
    tail_bytes = max(1, tail_kb) * 1024

    tail_content = ""
    if transcript_path:
        p = Path(transcript_path)
        if p.is_file():
            tail_content = read_tail(p, tail_bytes)
        else:
            tail_content = f"(transcript_path 不存在: {transcript_path})"

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"Auto-dump @ {ts} (PreCompact/{trigger})"
    header = json.dumps({
        "transcript_path": transcript_path,
        "trigger": trigger,
        "tail_bytes": tail_bytes,
    }, ensure_ascii=False, indent=2)

    content = (
        "[auto-dump metadata]\n"
        + header
        + "\n\n[transcript tail]\n"
        + (tail_content if tail_content else "(無 transcript 內容)")
    )

    return {
        "session_id": session_id,
        "title": title,
        "category": "auto-dump",
        "content": content,
        "tags": f"auto,precompact,{trigger}",
        "context_percentage": 0,
    }


def save_to_db(record: Dict[str, Any]) -> None:
    try:
        result = subprocess.run(
            ["python3", str(CTX_DB_PY), "save"],
            input=json.dumps(record, ensure_ascii=False),
            text=True, capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            log_error(
                f"ctx-db save failed rc={result.returncode} "
                f"stderr={result.stderr.strip()}"
            )
    except Exception as exc:
        log_error(f"save_to_db exception: {exc}")


def main() -> int:
    try:
        raw = sys.stdin.read() or ""
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        log_error(f"invalid stdin payload: {exc}")
        payload = {}

    try:
        config = load_config()
        record = build_record(payload, config)
        save_to_db(record)
    except Exception as exc:
        log_error(f"main exception: {exc}\n{traceback.format_exc()}")

    # 永遠 exit 0，不阻塞 compact
    return 0


if __name__ == "__main__":
    sys.exit(main())
