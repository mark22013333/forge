#!/usr/bin/env python3
"""redact.py — 密鑰清洗模組（純 stdlib，零 pip 依賴）

在 ctx-save 的資料流兩個關鍵入口套用，避免敏感字串外洩：
  1. ctx-autodump.py 寫入 DB 前：snapshot content 過一次
  2. ctx-reinject.py 注入 stderr 前：讀到的 snapshot content 再過一次

守中等保守路線：誤判寧可多（[REDACTED:...] 不可讀），不可漏（明文落地）。
回傳 (redacted_text, replaced_count)，呼叫端可用計數寫 log。
"""
from __future__ import annotations

import re
from typing import List, Tuple


# ---------------------------------------------------------------------------
# SIMPLE_PATTERNS：整段命中 → 整段換 [REDACTED:label]
# ---------------------------------------------------------------------------
# 順序重要：特定 pattern（Anthropic）要排在 generic（OpenAI）之前。
SIMPLE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Anthropic API key（優先於 generic sk-）
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{32,}"), "anthropic-key"),
    # OpenAI key（含 sk-proj- 變體）
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}"), "openai-key"),
    # AWS Access Key ID
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws-access-key"),
    # GitHub PAT / fine-grained token / refresh token / OAuth token / session token
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "github-token"),
    # Slack bot / app / user token
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "slack-token"),
    # JWT（三段 base64url）
    (
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"
        ),
        "jwt",
    ),
    # PEM 私鑰起始行（配對 footer 太貴，只清起始行即足以警示）
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private-key-header"),
    # Google OAuth refresh token
    (re.compile(r"\b1//0[A-Za-z0-9_\-]{40,}\b"), "google-oauth-refresh"),
]


# ---------------------------------------------------------------------------
# PREFIXED_PATTERNS：保留 prefix、只換 value
# regex 必須有 2 組：group(1)=keep prefix，group(2)=redact value
# ---------------------------------------------------------------------------
PREFIXED_PATTERNS: List[Tuple[re.Pattern, str]] = [
    # Authorization: Bearer <token>
    (
        re.compile(
            r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._\-]{16,})"
        ),
        "bearer",
    ),
    # password / passwd / pwd = value
    (
        re.compile(
            r"(?i)((?:password|passwd|pwd)\s*[=:]\s*['\"]?)"
            r"([^\s'\",;&]{4,128})"
        ),
        "password",
    ),
    # api_key / apikey / secret_key / access_token = value
    (
        re.compile(
            r"(?i)((?:api[_\-]?key|apikey|secret[_\-]?key|"
            r"access[_\-]?token)\s*[=:]\s*['\"]?)"
            r"([A-Za-z0-9_\-\.]{16,})"
        ),
        "generic-key",
    ),
    # AWS secret access key（近 40 字 base64）
    (
        re.compile(
            r"(?i)(aws[_\-]?secret[_\-]?(?:access[_\-]?)?key"
            r"['\"\s:=]+)([A-Za-z0-9/+=]{40})"
        ),
        "aws-secret",
    ),
]


def redact_secrets(text: str) -> Tuple[str, int]:
    """對 text 套所有 pattern，回傳 (redacted_text, replaced_count)。

    pure function — 不讀寫檔、不 log；呼叫端自行處理計數。
    text 為 None 或空字串時回傳原值，count=0。
    """
    if not text:
        return text or "", 0
    count = 0
    out = text
    for pat, label in SIMPLE_PATTERNS:
        placeholder = "[REDACTED:" + label + "]"
        new_out, n = pat.subn(placeholder, out)
        if n:
            count += n
            out = new_out
    for pat, label in PREFIXED_PATTERNS:
        label_str = label
        new_out, n = pat.subn(
            lambda m, _l=label_str:
                m.group(1) + "[REDACTED:" + _l + "]",
            out,
        )
        if n:
            count += n
            out = new_out
    return out, count


# ---------------------------------------------------------------------------
# 便利 helper：包一層不回傳計數（呼叫端不在乎替換了幾次時用）
# ---------------------------------------------------------------------------


def redact(text: str) -> str:
    """redact_secrets 的簡化版，只回清洗後文字。"""
    cleaned, _ = redact_secrets(text)
    return cleaned


if __name__ == "__main__":
    # 最小可執行 smoke test：從 stdin 讀、redact 後寫 stdout
    import sys
    raw = sys.stdin.read()
    cleaned, n = redact_secrets(raw)
    sys.stdout.write(cleaned)
    sys.stderr.write("[redact] " + str(n) + " secrets masked\n")
