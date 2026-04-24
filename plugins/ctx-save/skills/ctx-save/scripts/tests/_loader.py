"""共用模組載入器：處理 hyphen 檔名（ctx-db.py / ctx-distill.py ...）。

對齊 ctx-viewer.py::_load_ctx_db 的寫法。
所有 test_*.py 都從這裡取得已載入的 ctx_db / ctx_distill 模組，
避免重複 spec_from_file_location 樣板。
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent.parent


def _load_hyphen_module(filename: str, mod_name: str):
    """以 importlib 載入同目錄的 hyphen-named 腳本。"""
    path = SCRIPTS_DIR / filename
    if not path.exists():
        raise ImportError("target script not found: " + str(path))
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load " + filename)
    module = importlib.util.module_from_spec(spec)
    # 放進 sys.modules 讓內部 relative import 找得到
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def load_ctx_db():
    return _load_hyphen_module("ctx-db.py", "ctx_db")


def load_ctx_distill():
    return _load_hyphen_module("ctx-distill.py", "ctx_distill")


def load_analyze_module(dotted: str):
    """載入 analyze.xxx 子模組（正常 import 即可，無 hyphen）。

    前提：scripts/ 目錄已在 sys.path（由 run_all.py 或個別 test 保證）。
    """
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    return importlib.import_module(dotted)
