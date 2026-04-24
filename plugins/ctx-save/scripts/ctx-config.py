#!/usr/bin/env python3
"""ctx-config.py — ~/.ctx-save/config.json 讀寫

配置檔結構：
    {
      "mode": "off" | "assist" | "auto",
      "alert_threshold": 60,          // 觸發提醒的百分比
      "auto_dump_threshold": 70,      // auto 模式下觸發自動 raw dump 的百分比
      "cooldown_minutes": 15,         // 兩次提醒/dump 間的冷卻
      "dump_strategy": "raw_all",     // 保留全部 raw 對話（未來可擴 summary）
      "transcript_tail_kb": 200,      // auto-dump 讀 transcript 尾端的 KB 數
      "restore_strategy": "summary",  // v3：reinject 策略 off/summary/full/ask
      "max_reinject_bytes": 4096      // v3：reinject 單次注入上限（bytes）
    }

用法:
    ctx-config.py init                 寫入預設值（若不存在）
    ctx-config.py get <key>            取值（stdout 純文字，供 shell 使用）
    ctx-config.py get-all              輸出整個 JSON（一行）
    ctx-config.py set <key> <value>    更新單一鍵（type-aware）
    ctx-config.py path                 印出 config.json 絕對路徑

環境變數：
    CTX_SAVE_CONFIG_PATH   覆寫 config 路徑（預設 ~/.ctx-save/config.json）
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "assist",
    "alert_threshold": 60,
    "auto_dump_threshold": 70,
    "cooldown_minutes": 15,
    "dump_strategy": "raw_all",
    "transcript_tail_kb": 200,
    # v3 新增：compact 後自動還原閉環
    "restore_strategy": "summary",
    "max_reinject_bytes": 4096,
}

VALID_MODES = {"off", "assist", "auto"}
VALID_RESTORE_STRATEGIES = {"off", "summary", "full", "ask"}


def config_path() -> Path:
    override = os.environ.get("CTX_SAVE_CONFIG_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".ctx-save" / "config.json"


def load() -> Dict[str, Any]:
    path = config_path()
    if not path.exists():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    if isinstance(data, dict):
        merged.update(data)
    return merged


def save(data: Dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def coerce(key: str, raw: str) -> Any:
    if key == "mode":
        if raw not in VALID_MODES:
            raise ValueError(
                "mode must be one of: " + ", ".join(sorted(VALID_MODES))
            )
        return raw
    if key in (
        "alert_threshold",
        "auto_dump_threshold",
        "cooldown_minutes",
        "transcript_tail_kb",
        "max_reinject_bytes",
    ):
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} must be integer") from exc
        if key.endswith("threshold") and not (1 <= value <= 99):
            raise ValueError(f"{key} must be between 1 and 99")
        if key == "cooldown_minutes" and value < 0:
            raise ValueError("cooldown_minutes must be >= 0")
        if key == "transcript_tail_kb" and value < 1:
            raise ValueError("transcript_tail_kb must be >= 1")
        if key == "max_reinject_bytes" and value < 256:
            raise ValueError("max_reinject_bytes must be >= 256")
        return value
    if key == "dump_strategy":
        if raw not in {"raw_all"}:
            raise ValueError("dump_strategy must be: raw_all")
        return raw
    if key == "restore_strategy":
        if raw not in VALID_RESTORE_STRATEGIES:
            raise ValueError(
                "restore_strategy must be one of: "
                + ", ".join(sorted(VALID_RESTORE_STRATEGIES))
            )
        return raw
    raise ValueError(f"unknown key: {key}")


def cmd_init() -> int:
    path = config_path()
    if path.exists():
        print(json.dumps(
            {"status": "exists", "path": str(path)},
            ensure_ascii=False,
        ))
        return 0
    save(dict(DEFAULT_CONFIG))
    print(json.dumps(
        {"status": "created", "path": str(path), "config": DEFAULT_CONFIG},
        ensure_ascii=False,
    ))
    return 0


def cmd_get(argv) -> int:
    if len(argv) < 3:
        print("用法: ctx-config.py get <key>", file=sys.stderr)
        return 1
    key = argv[2]
    data = load()
    if key not in data:
        print(f"未知鍵: {key}", file=sys.stderr)
        return 1
    # shell friendly：純值輸出
    print(data[key])
    return 0


def cmd_get_all() -> int:
    print(json.dumps(load(), ensure_ascii=False))
    return 0


def cmd_set(argv) -> int:
    if len(argv) < 4:
        print("用法: ctx-config.py set <key> <value>", file=sys.stderr)
        return 1
    key, raw = argv[2], argv[3]
    data = load()
    if key not in DEFAULT_CONFIG:
        print(f"未知鍵: {key}（可用鍵：{', '.join(DEFAULT_CONFIG)}）", file=sys.stderr)
        return 1
    try:
        value = coerce(key, raw)
    except ValueError as exc:
        print(f"無效值: {exc}", file=sys.stderr)
        return 1
    data[key] = value
    path = save(data)
    print(json.dumps(
        {"status": "ok", "path": str(path), "config": data},
        ensure_ascii=False,
    ))
    return 0


def cmd_path() -> int:
    print(config_path())
    return 0


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "init":
        return cmd_init()
    if cmd == "get":
        return cmd_get(argv)
    if cmd == "get-all":
        return cmd_get_all()
    if cmd == "set":
        return cmd_set(argv)
    if cmd == "path":
        return cmd_path()
    print(f"未知指令: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
