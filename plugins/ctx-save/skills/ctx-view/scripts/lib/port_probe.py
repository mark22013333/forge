#!/usr/bin/env python3
"""Port 探測與衝突診斷模組。

提供三個公開函式：
    - is_port_available(port)        — 以 socket bind 測試可用性
    - diagnose_port_occupant(port)   — 透過 lsof 取得占用者資訊
    - find_available_port(start, ...) — 從起始 port 遞增尋找可用值
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from typing import Callable, Optional


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """嘗試 bind 指定 port，成功立即關閉 socket 並回 True。

    注意：僅檢查 TCP；預設 host 為 ``127.0.0.1``（本機 only，對應安全需求）。

    Args:
        port: 欲探測的 port。
        host: 綁定的網路介面（預設 127.0.0.1）。

    Returns:
        bool: True 表示可用；False 表示被占用或無權限。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 不設 SO_REUSEADDR — 要真實模擬後續啟動 server 的綁定情況
    try:
        sock.bind((host, port))
    except OSError:
        return False
    else:
        return True
    finally:
        try:
            sock.close()
        except OSError:
            pass


def diagnose_port_occupant(port: int) -> Optional[dict]:
    """呼叫 ``lsof`` 取得監聽指定 port 的程序資訊。

    命令：``lsof -nP -iTCP:{port} -sTCP:LISTEN``

    若下列任一情況發生，回傳 None（降級處理）：
        - lsof 不在 PATH
        - 命令執行超時（500ms）
        - 輸出無可解析行（無監聽者）

    Args:
        port: 欲診斷的 port。

    Returns:
        dict | None:
            - {"pid": int, "command": str}
            - 失敗或無結果 → None
    """
    if shutil.which("lsof") is None:
        return None

    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    stdout = result.stdout or ""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    # lsof 預設首行為標題：COMMAND PID USER FD TYPE ...
    if len(lines) < 2:
        return None

    # 解析第一筆資料列
    first_data_line = lines[1]
    parts = first_data_line.split()
    if len(parts) < 2:
        return None

    command = parts[0]
    try:
        pid = int(parts[1])
    except ValueError:
        return None

    return {"pid": pid, "command": command}


def find_available_port(
    start: int = 29898,
    max_tries: int = 10,
    on_conflict: Optional[Callable[[int, Optional[dict]], None]] = None,
) -> int:
    """從 ``start`` 遞增尋找可用 port。

    每次衝突時，若 ``on_conflict`` 非 None，則呼叫一次回報：
        on_conflict(conflicted_port, occupant_dict_or_None)

    Args:
        start: 起始 port（預設 29898）。
        max_tries: 最多嘗試次數（預設 10，即 29898~29907）。
        on_conflict: 衝突回呼；無則不回報。

    Returns:
        int: 可用的 port 號碼。

    Raises:
        RuntimeError: 全數嘗試失敗。
    """
    for offset in range(max_tries):
        candidate = start + offset
        if is_port_available(candidate):
            return candidate
        if on_conflict is not None:
            occupant = diagnose_port_occupant(candidate)
            try:
                on_conflict(candidate, occupant)
            except Exception:
                # 回呼失敗不應中斷探測流程
                pass

    last_port = start + max_tries - 1
    raise RuntimeError(
        f"無可用 port（已嘗試 {start}~{last_port}，共 {max_tries} 個）"
    )
