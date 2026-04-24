#!/usr/bin/env python3
"""ctx-save v3 測試套件入口。

使用:
    python run_all.py           # 跑全套
    python run_all.py -v        # 詳細模式
    python run_all.py -k rules  # 只跑名稱含 rules 的 test

以 stdlib unittest.loader.discover 自動撿 test_*.py，不依賴 pytest。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
# 把 scripts/ 加到 sys.path，讓 tests 內部可 from analyze.xxx import ...
SCRIPTS_DIR = HERE.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def main(argv: list[str]) -> int:
    verbosity = 1
    keyword = None
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "-v":
            verbosity = 2
        elif arg == "-k" and args:
            keyword = args.pop(0)
        elif arg in {"-h", "--help"}:
            print(__doc__)
            return 0

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(HERE), pattern="test_*.py")

    if keyword:
        suite = _filter_suite(suite, keyword)

    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def _filter_suite(
    suite: unittest.TestSuite,
    keyword: str,
) -> unittest.TestSuite:
    """遞迴過濾 suite：只保留 id() 含 keyword 的 test case。"""
    filtered = unittest.TestSuite()
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            sub = _filter_suite(item, keyword)
            if sub.countTestCases() > 0:
                filtered.addTest(sub)
        else:
            if keyword in item.id():
                filtered.addTest(item)
    return filtered


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
