#!/usr/bin/env python3
"""同步 plugins/<name>/.claude-plugin/plugin.json 的 version 到 marketplace.json。

單一真相來源：plugin.json。marketplace.json 是派生檔。

用法：
    python3 scripts/sync-versions.py           # 同步並寫回
    python3 scripts/sync-versions.py --check   # 只驗證，不寫入；不一致時 exit 1
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
PLUGINS_DIR = REPO_ROOT / "plugins"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, data: dict) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def collect_plugin_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for plugin_json in PLUGINS_DIR.glob("*/.claude-plugin/plugin.json"):
        data = load_json(plugin_json)
        name = data.get("name")
        version = data.get("version")
        if not name or not version:
            print(f"⚠️  跳過缺欄位的 plugin.json：{plugin_json}", file=sys.stderr)
            continue
        versions[name] = version
    return versions


def main() -> int:
    check_only = "--check" in sys.argv

    marketplace = load_json(MARKETPLACE_PATH)
    plugin_versions = collect_plugin_versions()

    drift: list[tuple[str, str, str]] = []
    updated = False

    for entry in marketplace.get("plugins", []):
        name = entry.get("name")
        if name not in plugin_versions:
            continue
        current = entry.get("version")
        expected = plugin_versions[name]
        if current != expected:
            drift.append((name, current, expected))
            if not check_only:
                entry["version"] = expected
                updated = True

    if check_only:
        if drift:
            print("❌ 版本不一致：", file=sys.stderr)
            for name, cur, exp in drift:
                print(f"   - {name}: marketplace={cur} plugin.json={exp}", file=sys.stderr)
            print("\n執行 `python3 scripts/sync-versions.py` 修正。", file=sys.stderr)
            return 1
        print("✅ 版本一致")
        return 0

    if updated:
        dump_json(MARKETPLACE_PATH, marketplace)
        print("✅ 已同步版本：")
        for name, cur, exp in drift:
            print(f"   - {name}: {cur} → {exp}")
    else:
        print("✅ 無需變更，版本已一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
