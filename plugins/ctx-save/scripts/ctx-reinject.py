#!/usr/bin/env python3
"""ctx-reinject.py — SessionStart(matcher=compact) hook：compact 後自動還原閉環

Claude Code SessionStart hook 在 matcher="compact" 觸發時傳入 stdin JSON：
    {
      "session_id": "...",
      "hook_event_name": "SessionStart",
      "source": "compact",
      ...
    }

設計原則（arch.md §2.4 Hook 契約）：
  - 永不中斷 Claude Code：最外層 try/except Exception → exit 0
  - 錯誤靜默落地到 ~/.cache/ctx-save/reinject-error.log
  - 注入內容寫入 stderr（Claude Code 標準 context 注入點）
  - 依 config.restore_strategy 決定還原格式：off / summary / full / ask
  - max_reinject_bytes 限制單次注入量，避免再次撐爆 context

策略模式（arch.md §2.3）：
  OffStrategy / SummaryStrategy / FullStrategy / AskStrategy 各自渲染 payload。
  新增策略只需加 class 並加入 STRATEGIES dict，不動主流程。
"""

import importlib.util
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional, Protocol

# ---------------------------------------------------------------------------
# 常數與路徑
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
CTX_DB_PY = PLUGIN_ROOT / "skills" / "ctx-save" / "scripts" / "ctx-db.py"
CTX_CONFIG_PY = SCRIPT_DIR / "ctx-config.py"
REDACT_PY = SCRIPT_DIR / "redact.py"

ERROR_LOG_DIR = Path.home() / ".cache" / "ctx-save"
ERROR_LOG = ERROR_LOG_DIR / "reinject-error.log"

COOLDOWN_DIR = Path("/tmp/claude")
COOLDOWN_FILE = COOLDOWN_DIR / "ctx-reinject-cooldown"
COOLDOWN_SECONDS = 10 * 60  # 10 分鐘

DEFAULT_STRATEGY = "summary"
DEFAULT_MAX_REINJECT_BYTES = 4096


# ---------------------------------------------------------------------------
# 錯誤 log（嚴格靜默；寫 log 失敗也吞）
# ---------------------------------------------------------------------------


def log_error(msg: str) -> None:
    """寫錯誤訊息到 ~/.cache/ctx-save/reinject-error.log；任何失敗都吞。"""
    try:
        ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with ERROR_LOG.open("a", encoding="utf-8") as f:
            f.write("[" + ts + "] " + msg + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 動態載入 ctx-db（skill 層 Facade）
# ---------------------------------------------------------------------------


def _load_ctxdb():
    """以 importlib 動態載入 skills/ctx-save/scripts/ctx-db.py。

    失敗時回 None，由呼叫端決定後續行為。
    """
    try:
        spec = importlib.util.spec_from_file_location("ctxdb", CTX_DB_PY)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        log_error("load_ctxdb failed: " + str(exc))
        return None


def _load_redact():
    """動態載入 plugin 層 redact.py（S-7 第二道防線）。失敗回 None。"""
    try:
        spec = importlib.util.spec_from_file_location("redact", REDACT_PY)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as exc:
        log_error("load_redact failed: " + str(exc))
        return None


# ---------------------------------------------------------------------------
# Config 讀取（不依賴 ctx-config.py subprocess，直接讀 JSON）
# ---------------------------------------------------------------------------


def load_config() -> Dict[str, Any]:
    """讀 ~/.ctx-save/config.json；缺漏鍵填預設值。

    只關心 reinject 用得到的兩個鍵：
      restore_strategy: off/summary/full/ask（預設 summary）
      max_reinject_bytes: int（預設 4096）
    """
    override = os.environ.get("CTX_SAVE_CONFIG_PATH")
    path = Path(override).expanduser() if override else \
        Path.home() / ".ctx-save" / "config.json"
    defaults = {
        "restore_strategy": DEFAULT_STRATEGY,
        "max_reinject_bytes": DEFAULT_MAX_REINJECT_BYTES,
    }
    if not path.exists():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return defaults
        strategy = data.get("restore_strategy", DEFAULT_STRATEGY)
        if strategy not in {"off", "summary", "full", "ask"}:
            strategy = DEFAULT_STRATEGY
        try:
            max_bytes = int(data.get("max_reinject_bytes",
                                      DEFAULT_MAX_REINJECT_BYTES))
        except (TypeError, ValueError):
            max_bytes = DEFAULT_MAX_REINJECT_BYTES
        if max_bytes < 256:
            max_bytes = 256
        return {
            "restore_strategy": strategy,
            "max_reinject_bytes": max_bytes,
        }
    except (json.JSONDecodeError, OSError) as exc:
        log_error("load_config failed: " + str(exc))
        return defaults


# ---------------------------------------------------------------------------
# 冷卻機制（同一 session 10 分鐘內不重複注入）
# ---------------------------------------------------------------------------


def _cooldown_key(session_id: str, strategy: str = "") -> str:
    """產生 cooldown 檔內的單一 session 標識。

    v3.0.1：加入 strategy 版本戳 — 使用者切換 restore_strategy
    後不應被舊 strategy 的 cooldown 卡住（使用者剛改設定就應該
    能看到新 strategy 的效果）。
    """
    sid = session_id or "(unknown)"
    return sid if not strategy else sid + ":" + strategy


def _read_cooldown() -> Dict[str, float]:
    """讀 cooldown 檔：{session_id:strategy: epoch_seconds}。"""
    if not COOLDOWN_FILE.exists():
        return {}
    try:
        data = json.loads(COOLDOWN_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items()
                    if isinstance(v, (int, float))}
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return {}


def _write_cooldown(data: Dict[str, float]) -> None:
    """寫回 cooldown 檔；失敗靜默。"""
    try:
        COOLDOWN_DIR.mkdir(parents=True, exist_ok=True)
        COOLDOWN_FILE.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        log_error("write_cooldown failed: " + str(exc))


def is_in_cooldown(session_id: str, strategy: str = "") -> bool:
    """檢查此 (session, strategy) 是否在冷卻期內。"""
    now = time.time()
    data = _read_cooldown()
    key = _cooldown_key(session_id, strategy)
    last = data.get(key)
    if last is None:
        return False
    return (now - last) < COOLDOWN_SECONDS


def mark_cooldown(session_id: str, strategy: str = "") -> None:
    """標記此 (session, strategy) 剛注入過；順便清掉超過 24h 的舊 entry。"""
    now = time.time()
    data = _read_cooldown()
    data[_cooldown_key(session_id, strategy)] = now
    # 垃圾回收：保留 24h 內的紀錄
    cutoff = now - 24 * 3600
    data = {k: v for k, v in data.items() if v >= cutoff}
    _write_cooldown(data)


# ---------------------------------------------------------------------------
# RestoreStrategy（策略模式）
# ---------------------------------------------------------------------------


class RestoreStrategy(Protocol):
    """還原策略介面：吃一筆 snapshot dict，回傳要寫進 stderr 的字串。

    回傳空字串代表「不注入」（交由呼叫端決定是否標記 restored_at）。
    """
    name: str

    def render(self, snapshot: Dict[str, Any], max_bytes: int) -> str: ...


class OffStrategy:
    """off：完全不注入，silent exit。"""
    name = "off"

    def render(self, snapshot: Dict[str, Any], max_bytes: int) -> str:
        return ""


class SummaryStrategy:
    """summary（預設）：輸出 title + 關鍵 metadata + content 前 N 字。

    受 max_bytes 限制，避免再次撐爆 context。
    """
    name = "summary"

    def render(self, snapshot: Dict[str, Any], max_bytes: int) -> str:
        title = snapshot.get("title") or "(untitled)"
        created_at = snapshot.get("created_at") or ""
        ctx_pct = snapshot.get("context_percentage")
        session_id = snapshot.get("session_id") or ""
        content = snapshot.get("content") or ""

        header = [
            "## Restored from last compact",
            "",
            "_ctx-save auto-reinject · summary mode_",
            "",
            "- Snapshot title: " + str(title),
            "- Created at: " + str(created_at),
            "- Source session: " + str(session_id),
        ]
        if ctx_pct is not None:
            header.append(
                "- Context @ snapshot: " + str(ctx_pct) + "%"
            )
        header.extend([
            "",
            "### Excerpt",
            "",
        ])
        head_text = "\n".join(header) + "\n"

        footer = (
            "\n\n---\n完整快照：執行 /ctx-save restore --latest-compact 查看\n"
        )

        # 預留 header + footer 的 byte 額度，剩餘給 content
        fixed_bytes = len(head_text.encode("utf-8")) + \
            len(footer.encode("utf-8"))
        budget = max(256, max_bytes - fixed_bytes)
        excerpt = _truncate_utf8(content, budget)
        return head_text + excerpt + footer


class FullStrategy:
    """full：注入完整 content（仍受 max_bytes 硬上限保護）。"""
    name = "full"

    def render(self, snapshot: Dict[str, Any], max_bytes: int) -> str:
        title = snapshot.get("title") or "(untitled)"
        created_at = snapshot.get("created_at") or ""
        content = snapshot.get("content") or ""
        header = (
            "## Restored from last compact\n"
            "\n"
            "_ctx-save auto-reinject · full mode_\n"
            "\n"
            "- Snapshot title: " + str(title) + "\n"
            "- Created at: " + str(created_at) + "\n"
            "\n"
            "### Full content\n\n"
        )
        fixed_bytes = len(header.encode("utf-8"))
        budget = max(256, max_bytes - fixed_bytes)
        return header + _truncate_utf8(content, budget)


class AskStrategy:
    """ask：只丟提示，讓 Claude（或使用者）決定是否還原。"""
    name = "ask"

    def render(self, snapshot: Dict[str, Any], max_bytes: int) -> str:
        title = snapshot.get("title") or "(untitled)"
        created_at = snapshot.get("created_at") or ""
        return (
            "## Restored from last compact\n"
            "\n"
            "_ctx-save auto-reinject · ask mode_\n"
            "\n"
            "發現 compact 前的 snapshot：\n"
            "- " + str(title) + "（" + str(created_at) + "）\n"
            "\n"
            "如需完整還原，請執行："
            " `/ctx-save restore --latest-compact`\n"
        )


STRATEGIES: Dict[str, RestoreStrategy] = {
    "off": OffStrategy(),
    "summary": SummaryStrategy(),
    "full": FullStrategy(),
    "ask": AskStrategy(),
}


def _truncate_utf8(text: str, max_bytes: int) -> str:
    """以 utf-8 byte 長度為界截斷文字；尾端補 `…`。"""
    if not text:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    # 粗略切 byte，再回解成字串；避免半個 code point
    sliced = encoded[:max_bytes]
    return sliced.decode("utf-8", errors="ignore") + "\n…"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _read_payload() -> Dict[str, Any]:
    """讀取 stdin 的 hook payload；解析失敗回空 dict。"""
    try:
        raw = sys.stdin.read() or ""
        if not raw.strip():
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log_error("invalid stdin payload: " + str(exc))
        return {}


def _resolve_db_path() -> Optional[Path]:
    """解析中央 DB 路徑；環境變數覆寫優先。"""
    override = os.environ.get("CTX_SAVE_DB_PATH")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".ctx-save" / "context.db"


def _resolve_project_path() -> Optional[str]:
    """解析當前 project_path（POSIX 字串），對齊 ctx-db 的寫入規則。

    避免 SessionStart=compact 時把「其他專案」的 unrestored autodump
    拉回來注入，造成跨專案脈絡污染。
    """
    try:
        value = os.environ.get("CTX_PROJECT")
        if value:
            path = Path(value).expanduser().resolve()
        else:
            path = Path.cwd().resolve()
        return str(PurePosixPath(path.as_posix()))
    except Exception as exc:
        log_error("_resolve_project_path failed: " + str(exc))
        return None


def _do_reinject(payload: Dict[str, Any]) -> int:
    """執行完整 reinject 流程：載入 config → 查 DB → render → 寫 stderr。

    Returns:
        exit code（永遠 0，符合 Hook 契約）。
    """
    session_id = str(payload.get("session_id") or "")
    config = load_config()
    strategy_name = config["restore_strategy"]
    max_bytes = int(config["max_reinject_bytes"])

    # off 策略：直接靜默退出
    if strategy_name == "off":
        return 0

    # 冷卻期內：不重複注入（cooldown 綁 strategy，切換後自動失效）
    if is_in_cooldown(session_id, strategy_name):
        return 0

    db_path = _resolve_db_path()
    if db_path is None or not db_path.exists():
        # DB 不存在是合理狀態（尚未累積任何 auto-dump）
        return 0

    ctxdb = _load_ctxdb()
    if ctxdb is None:
        log_error("ctx-db module unavailable; skip reinject")
        return 0

    # 找最新未被還原的 auto-dump（務必帶 project_path，避免跨專案污染）
    project_path = _resolve_project_path()
    try:
        snapshot = ctxdb.find_latest_unrestored_autodump(
            db_path,
            session_id=session_id or None,
            project_path=project_path,
        )
    except Exception as exc:
        log_error("find_latest_unrestored_autodump failed: " + str(exc))
        return 0

    if snapshot is None:
        # 沒 snapshot 是合理情境（首次 compact / 已全數還原）；silent exit
        return 0

    # v3 S-7：注入 stderr 前再過一次 redact。防護歷史資料（v2.3 寫入時
    # 未清洗）、以及跨機同步 DB 後的殘留明文。
    redact_mod = _load_redact()
    if redact_mod is not None:
        try:
            cleaned, n = redact_mod.redact_secrets(snapshot.get("content") or "")
            snapshot["content"] = cleaned
            if n:
                log_error(
                    "redacted " + str(n) + " secrets at reinject time"
                )
        except Exception as exc:
            log_error("redact_secrets at reinject failed: " + str(exc))

    strategy = STRATEGIES.get(strategy_name, STRATEGIES[DEFAULT_STRATEGY])

    try:
        rendered = strategy.render(snapshot, max_bytes)
    except Exception as exc:
        log_error("strategy.render failed (" + strategy_name + "): " + str(exc))
        return 0

    if not rendered:
        return 0

    # 寫 stderr（Claude Code 的 context 注入點）
    try:
        sys.stderr.write(rendered)
        sys.stderr.flush()
    except Exception as exc:
        log_error("stderr write failed: " + str(exc))
        return 0

    # 標記已還原 + 更新 compact event + 冷卻
    try:
        ctxdb.mark_restored(db_path, snapshot["id"])
    except Exception as exc:
        log_error("mark_restored failed: " + str(exc))

    try:
        # 優先用 find_latest_unrestored_autodump 回傳的 latest_compact_event_id
        # （精確標記一筆）；fallback 走 snapshot_id 以相容舊 Facade。
        event_id = snapshot.get("latest_compact_event_id")
        if event_id is not None:
            ctxdb.mark_compact_event_reinjected(db_path, event_id=int(event_id))
        else:
            ctxdb.mark_compact_event_reinjected(
                db_path, snapshot_id=snapshot["id"]
            )
    except Exception as exc:
        log_error("mark_compact_event_reinjected failed: " + str(exc))

    try:
        mark_cooldown(session_id, strategy_name)
    except Exception as exc:
        log_error("mark_cooldown failed: " + str(exc))

    return 0


def main() -> int:
    """最外層兜底；絕不中斷 Claude Code。"""
    try:
        payload = _read_payload()
        return _do_reinject(payload)
    except Exception as exc:
        log_error(
            "unhandled exception: " + str(exc) + "\n" + traceback.format_exc()
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
