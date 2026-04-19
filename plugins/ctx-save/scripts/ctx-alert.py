#!/usr/bin/env python3
"""ctx-alert.py — PostToolUse hook：依 config 的 mode 決定提醒/自動 dump

讀取 statusline JSON（context_window.used_percentage）+ ~/.ctx-save/config.json。

行為矩陣：
    mode=off         什麼都不做
    mode=assist      >= alert_threshold 時輸出提醒（遵守冷卻）
    mode=auto        >= alert_threshold 輸出提醒
                     >= auto_dump_threshold 另外呼叫 ctx-autodump.py raw dump

Claude Code 透過 stdin 傳 PostToolUse payload（含 session_id, transcript_path）。
dump 需要 transcript_path，所以 auto dump 會把 stdin payload 原封轉給 autodump。
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
CTX_CONFIG_PY = SCRIPT_DIR / "ctx-config.py"
CTX_AUTODUMP_PY = SCRIPT_DIR / "ctx-autodump.py"

STATUSLINE_PATH = Path("/tmp/claude/statusline-last-input.json")
COOLDOWN_DIR = Path("/tmp/claude")
COOLDOWN_ALERT = COOLDOWN_DIR / "ctx-alert-cooldown"
COOLDOWN_AUTODUMP = COOLDOWN_DIR / "ctx-autodump-cooldown"


def log_error(msg: str) -> None:
    log_dir = Path.home() / ".cache" / "ctx-save"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with (log_dir / "alert-error.log").open("a", encoding="utf-8") as f:
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
        log_error(f"load_config: {exc}")
    return {
        "mode": "assist",
        "alert_threshold": 60,
        "auto_dump_threshold": 70,
        "cooldown_minutes": 15,
    }


def read_statusline_pct() -> Optional[int]:
    if not STATUSLINE_PATH.is_file():
        return None
    try:
        data = json.loads(STATUSLINE_PATH.read_text(encoding="utf-8"))
        pct = data.get("context_window", {}).get("used_percentage", 0)
        return int(pct)
    except Exception:
        return None


def in_cooldown(marker: Path, minutes: int) -> bool:
    if not marker.is_file() or minutes <= 0:
        return False
    try:
        last = int(marker.read_text().strip())
    except Exception:
        return False
    return (int(datetime.now().timestamp()) - last) < (minutes * 60)


def touch_cooldown(marker: Path) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(int(datetime.now().timestamp())))
    except Exception as exc:
        log_error(f"touch_cooldown {marker}: {exc}")


def trigger_autodump(stdin_payload: str) -> None:
    try:
        subprocess.run(
            ["python3", str(CTX_AUTODUMP_PY)],
            input=stdin_payload, text=True, timeout=15,
            capture_output=True,
        )
    except Exception as exc:
        log_error(f"trigger_autodump: {exc}")


def main() -> int:
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    except Exception:
        raw = ""

    config = load_config()
    mode = config.get("mode", "assist")
    if mode == "off":
        return 0

    pct = read_statusline_pct()
    if pct is None or pct == 0:
        return 0

    alert_th = int(config.get("alert_threshold", 60))
    dump_th = int(config.get("auto_dump_threshold", 70))
    cooldown = int(config.get("cooldown_minutes", 15))

    # auto dump（超過 dump_threshold 且 mode=auto）
    if mode == "auto" and pct >= dump_th:
        if not in_cooldown(COOLDOWN_AUTODUMP, cooldown):
            trigger_autodump(raw)
            touch_cooldown(COOLDOWN_AUTODUMP)
            # 也記一次 alert 冷卻，避免下一輪再印提醒
            touch_cooldown(COOLDOWN_ALERT)
            print(
                f"📦 Context 使用率 {pct}% ≥ 自動 dump 閾值 {dump_th}%，"
                f"已觸發 auto-dump（category=auto-dump）。可執行 /ctx-save:ctx-view 瀏覽。"
            )
            return 0
        # 仍在冷卻，退到 alert 邏輯

    # alert（assist / auto 共用）
    if pct >= alert_th and not in_cooldown(COOLDOWN_ALERT, cooldown):
        mode_hint = "（auto 模式：閾值 " + str(dump_th) + "% 將自動 dump）" if mode == "auto" else ""
        print(
            f"⚠️ Context 使用率 {pct}%（提醒閾值 {alert_th}%），"
            f"建議執行 /ctx-save 保存重要對話資訊{mode_hint}"
        )
        touch_cooldown(COOLDOWN_ALERT)

    return 0


if __name__ == "__main__":
    sys.exit(main())
