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

v3 擴充：
  - 讀 /tmp/claude/statusline-last-input.json 補 context_percentage
  - save 成功後以 ctx-db 的 record_compact_event 寫入 compact 事件時間軸
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
CTX_DB_PY = PLUGIN_ROOT / "skills" / "ctx-save" / "scripts" / "ctx-db.py"
CTX_CONFIG_PY = SCRIPT_DIR / "ctx-config.py"
REDACT_PY = SCRIPT_DIR / "redact.py"

STATUSLINE_PATH = Path("/tmp/claude/statusline-last-input.json")

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
    return "autodump" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def read_statusline_context_pct() -> Optional[float]:
    """讀 /tmp/claude/statusline-last-input.json 取 used_percentage。

    Claude Code 實際格式為 `{"context_window": {"used_percentage": N, ...}, ...}`，
    同時相容幾個早期平鋪鍵。解析失敗或缺欄位回 None。
    statusline 檔可能過期（compact 觸發前 CC 已重算），但誤差可接受。
    """
    try:
        if not STATUSLINE_PATH.exists():
            return None
        data = json.loads(STATUSLINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log_error("read statusline failed: " + str(exc))
        return None
    if not isinstance(data, dict):
        return None
    # 優先讀巢狀 context_window.used_percentage（Claude Code 官方結構）
    cw = data.get("context_window")
    if isinstance(cw, dict):
        val = cw.get("used_percentage")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    # 向下相容：平鋪鍵
    for key in ("used_percentage", "context_percentage", "used_pct"):
        if key in data:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                pass
    return None


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

    # v3 S-7：寫入 DB 前先過一遍 redact（防 API key / Bearer / password 外洩）
    redact_mod = _load_redact()
    if redact_mod is not None:
        try:
            content, redacted_n = redact_mod.redact_secrets(content)
            if redacted_n:
                log_error(
                    "redacted " + str(redacted_n) + " secrets in auto-dump"
                )
        except Exception as exc:
            log_error("redact_secrets failed: " + str(exc))

    # v3：從 statusline 取實際 context_percentage（取不到則 0）
    ctx_pct = read_statusline_context_pct()
    if ctx_pct is None:
        ctx_pct = 0

    return {
        "session_id": session_id,
        "title": title,
        "category": "auto-dump",
        "content": content,
        "tags": f"auto,precompact,{trigger}",
        "context_percentage": ctx_pct,
    }


def _load_ctxdb():
    """動態載入 skill 層 ctx-db.py。失敗回 None。"""
    try:
        spec = importlib.util.spec_from_file_location("ctxdb", CTX_DB_PY)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        log_error("load ctxdb failed: " + str(exc))
        return None


def _load_redact():
    """動態載入 plugin 層 redact.py。失敗回 None，呼叫端退化為不清洗。"""
    try:
        spec = importlib.util.spec_from_file_location("redact", REDACT_PY)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        log_error("load redact failed: " + str(exc))
        return None


def _save_via_subprocess(record: Dict[str, Any]) -> None:
    """Fallback：若 importlib 載入失敗，退回原本的 subprocess 模式。

    v2.3 既有契約保留；compact event 此模式下無法寫入。
    """
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
        log_error(f"save_to_db subprocess exception: {exc}")


def save_to_db(record: Dict[str, Any], trigger: str) -> None:
    """寫 auto-dump 到中央 DB，成功後記錄一筆 compact event。

    優先 importlib 直接呼叫 Facade，失敗回退 subprocess。
    """
    ctxdb = _load_ctxdb()
    if ctxdb is None:
        _save_via_subprocess(record)
        return

    try:
        db_path = ctxdb.get_db_path()
        ctxdb.save(db_path, [record])
    except Exception as exc:
        log_error(f"ctxdb.save exception: {exc}")
        # 出錯就不再嘗試寫 compact event，避免孤兒資料
        return

    # v3：補記 compact 事件時間軸（取最新同 session 的 auto-dump id）
    try:
        snap = ctxdb.find_latest_unrestored_autodump(
            db_path, session_id=record.get("session_id"),
        )
        snapshot_id = snap["id"] if snap else None
        ctxdb.record_compact_event(
            db_path,
            session_id=record.get("session_id", ""),
            project_path=record.get("project_path") or "(unknown)",
            happened_at=datetime.now(),
            trigger=trigger,
            context_pct=record.get("context_percentage"),
            snapshot_id=snapshot_id,
        )
    except Exception as exc:
        log_error(f"record_compact_event exception: {exc}")


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
        trigger = str(payload.get("trigger", "auto"))
        save_to_db(record, trigger)
    except Exception as exc:
        log_error(f"main exception: {exc}\n{traceback.format_exc()}")

    # 永遠 exit 0，不阻塞 compact
    return 0


if __name__ == "__main__":
    sys.exit(main())
