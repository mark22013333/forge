#!/usr/bin/env python3
"""ctx-mode.py — 使用者友善的 ctx-save 模式切換器

對應 /ctx-mode slash command。把 ctx-config.py 的冗長指令包成一行搞定的短語法。

用法：
    ctx-mode.py                    顯示當前配置
    ctx-mode.py off|assist|auto    切換模式
    ctx-mode.py alert <N>          改提醒閾值（1-99）
    ctx-mode.py dump <N>           改 auto dump 閾值（1-99）
    ctx-mode.py cooldown <N>       改冷卻分鐘（>=0）
    ctx-mode.py tail <KB>          改 transcript 尾端 KB（>=1）
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CTX_CONFIG_PY = SCRIPT_DIR / "ctx-config.py"

MODE_ALIAS = {"off", "assist", "auto"}
KEY_ALIAS = {
    "alert": "alert_threshold",
    "dump": "auto_dump_threshold",
    "cooldown": "cooldown_minutes",
    "tail": "transcript_tail_kb",
}

MODE_LABEL = {
    "off": "🔕 off（完全靜默，僅 PreCompact 保底）",
    "assist": "💬 assist（超過閾值時提醒）",
    "auto": "🤖 auto（提醒 + 自動 dump）",
}


def run_config(*args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["python3", str(CTX_CONFIG_PY), *args],
        capture_output=True, text=True,
    )
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def load_config() -> dict:
    rc, out, err = run_config("get-all")
    if rc != 0:
        print(f"❌ 讀取配置失敗: {err}", file=sys.stderr)
        sys.exit(1)
    return json.loads(out)


def show(cfg: dict) -> None:
    print("📋 ctx-save 配置")
    print(f"   模式         : {MODE_LABEL.get(cfg['mode'], cfg['mode'])}")
    print(f"   提醒閾值     : {cfg['alert_threshold']}%")
    print(f"   自動 dump    : {cfg['auto_dump_threshold']}%")
    print(f"   冷卻時間     : {cfg['cooldown_minutes']} 分鐘")
    print(f"   dump 策略    : {cfg['dump_strategy']}")
    print(f"   transcript   : {cfg['transcript_tail_kb']} KB")
    rc, out, _ = run_config("path")
    if rc == 0:
        print(f"   config 路徑  : {out}")


def set_key(key: str, value: str) -> None:
    rc, _, err = run_config("set", key, value)
    if rc != 0:
        print(f"❌ 更新失敗: {err}", file=sys.stderr)
        sys.exit(1)


def print_usage() -> None:
    print(
        "用法:\n"
        "  /ctx-mode                顯示當前配置\n"
        "  /ctx-mode off            關閉提醒與自動 dump\n"
        "  /ctx-mode assist         只提醒（預設）\n"
        "  /ctx-mode auto           提醒 + 自動 dump\n"
        "  /ctx-mode alert <N>      改提醒閾值（1-99）\n"
        "  /ctx-mode dump <N>       改 auto dump 閾值（1-99）\n"
        "  /ctx-mode cooldown <N>   改冷卻分鐘（>=0）\n"
        "  /ctx-mode tail <KB>      改 transcript 尾端 KB（>=1）",
        file=sys.stderr,
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not args:
        show(load_config())
        return 0

    first = args[0].lower()

    if first in MODE_ALIAS:
        set_key("mode", first)
        print(f"✅ 模式已切換為 {first}")
        print()
        show(load_config())
        return 0

    if first in KEY_ALIAS:
        if len(args) < 2:
            print(f"❌ {first} 需要指定數值", file=sys.stderr)
            print_usage()
            return 1
        real_key = KEY_ALIAS[first]
        set_key(real_key, args[1])
        print(f"✅ {real_key} 已更新為 {args[1]}")
        print()
        show(load_config())
        return 0

    print(f"❌ 未知參數: {' '.join(args)}", file=sys.stderr)
    print_usage()
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
