#!/usr/bin/env python3
"""ctx-viewer.py — Context Save 視覺化 Web Server (v2.1)

啟動方式:
    python3 ctx-viewer.py                          # 預設 ~/.ctx-save/context.db
    python3 ctx-viewer.py --db /path/to/context.db  # 指定 DB 路徑
    python3 ctx-viewer.py --port 29898               # 指定 port（預設 29898）
    python3 ctx-viewer.py --open                    # 啟動後自動開啟瀏覽器

無需任何外部依賴，僅使用 Python 標準庫。

v2.1 主要變更:
- 集中式 DB（~/.ctx-save/context.db）
- 新增 /api/projects 端點 + sidebar 專案下拉
- CSRF（Origin/Referer）+ HMAC confirm_token 二次確認
- 5000 筆分段批次刪除
- 前端 XSS 全面以 textContent / dataset / addEventListener 改寫
"""

import argparse
import hashlib
import hmac
import importlib.util
import json
import logging
import os
import re
import secrets
import sqlite3
import sys
import traceback
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

# --------------------------------------------------------------------------
# 模組級常數（arch.md §2.2.1）
# --------------------------------------------------------------------------

VERSION = "2.1.0"
SERVICE_NAME = "ctx-viewer"
STARTED_AT = datetime.utcnow().isoformat() + "Z"
CONFIRM_SECRET = secrets.token_bytes(32)
ALLOWED_ORIGINS = {"127.0.0.1", "localhost"}
MAX_BODY_BYTES = 65536
MAX_SEARCH_LEN = 200
SESSIONS_LIMIT_MAX = 500
BATCH_SIZE = 5000

# batch-delete before/after 欄位的日期格式（normalize 後匹配）
# 對齊 SQLite CURRENT_TIMESTAMP 的 'YYYY-MM-DD HH:MM:SS' 或單純日期
DATE_FILTER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?$")

# main() 會覆寫
DB_PATH: Optional[Path] = None

# Log 設定
logger = logging.getLogger("ctx-viewer")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("  [%(asctime)s] %(levelname)s %(message)s",
                                      datefmt="%H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)


# --------------------------------------------------------------------------
# ctx-db 模組載入（檔名含 dash，必走 importlib）
# --------------------------------------------------------------------------

def _load_ctx_db():
    """以 importlib 載入同目錄的 ctx-db.py（檔名含 dash 無法 import）"""
    here = Path(__file__).resolve().parent
    ctx_db_path = here / "ctx-db.py"
    spec = importlib.util.spec_from_file_location("ctx_db", ctx_db_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load ctx-db from {ctx_db_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CTX_DB = _load_ctx_db()


# --------------------------------------------------------------------------
# 前端 HTML + JS（raw string）
# --------------------------------------------------------------------------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Context Save Viewer</title>
<style>
  :root {
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #1c2333;
    --border: #30363d;
    --text: #e6edf3;
    --text2: #8b949e;
    --accent: #58a6ff;
    --accent2: #3fb950;
    --warn: #d29922;
    --danger: #f85149;
    --purple: #bc8cff;
    --radius: 8px;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text);
    display: flex; flex-direction: column; height: 100vh;
  }

  /* Header */
  .header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    padding: 12px 24px; display: flex; align-items: center; gap: 16px;
    flex-shrink: 0;
  }
  .header h1 { font-size: 18px; font-weight: 600; white-space: nowrap; }
  .header h1 span { color: var(--accent); }
  .header-sub {
    font-size: 12px; color: var(--text2);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-width: 360px;
  }
  .search-box {
    flex: 1; max-width: 400px;
    background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 8px 12px; color: var(--text); font-size: 14px; outline: none;
  }
  .search-box:focus { border-color: var(--accent); }
  .search-box::placeholder { color: var(--text2); }
  .header-stats { display: flex; gap: 16px; font-size: 13px; color: var(--text2); }
  .header-stats strong { color: var(--text); }

  .btn-batch-delete {
    background: transparent; border: 1px solid var(--border);
    color: var(--danger); padding: 6px 12px; border-radius: var(--radius);
    font-size: 13px; cursor: pointer; transition: background 0.15s;
  }
  .btn-batch-delete:hover { background: rgba(248, 81, 73, 0.1); }

  /* Layout */
  .main { display: flex; flex: 1; overflow: hidden; }

  /* Sidebar */
  .sidebar {
    width: 340px; min-width: 340px; background: var(--surface);
    border-right: 1px solid var(--border); display: flex; flex-direction: column;
    overflow: hidden;
  }
  .sidebar-header {
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    font-size: 13px; color: var(--text2);
    display: flex; flex-direction: column; gap: 8px;
  }
  .sidebar-header-row {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .sidebar-header select {
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 4px; padding: 4px 8px; font-size: 12px; outline: none;
    max-width: 100%;
  }
  .session-list { flex: 1; overflow-y: auto; }
  .session-item {
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    cursor: pointer; transition: background 0.15s;
  }
  .session-item:hover { background: var(--surface2); }
  .session-item.active { background: var(--surface2); border-left: 3px solid var(--accent); }
  .session-date { font-size: 12px; color: var(--text2); margin-bottom: 4px; }
  .session-title {
    font-size: 14px; font-weight: 500; margin-bottom: 6px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .session-project {
    font-size: 11px; color: var(--text2); margin-bottom: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .session-meta { display: flex; gap: 8px; flex-wrap: wrap; }
  .category-badge {
    font-size: 11px; padding: 2px 8px; border-radius: 12px;
    background: var(--bg); border: 1px solid var(--border);
  }
  .ctx-badge {
    font-size: 11px; padding: 2px 8px; border-radius: 12px;
    font-weight: 600;
  }
  .ctx-low { background: #0d3321; color: var(--accent2); border: 1px solid #238636; }
  .ctx-mid { background: #3d2e00; color: var(--warn); border: 1px solid #9e6a03; }
  .ctx-high { background: #3d1213; color: var(--danger); border: 1px solid #da3633; }

  /* Content */
  .content { flex: 1; overflow-y: auto; padding: 24px; }
  .empty-state {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100%; color: var(--text2); gap: 12px;
  }
  .empty-state .icon { font-size: 48px; opacity: 0.5; }
  .detail-header { margin-bottom: 24px; }
  .detail-header h2 { font-size: 22px; margin-bottom: 8px; }
  .detail-header-top {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    margin-bottom: 8px;
  }
  .detail-meta { font-size: 13px; color: var(--text2); display: flex; gap: 16px; flex-wrap: wrap; }

  .record-card {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    margin-bottom: 16px; overflow: hidden;
  }
  .record-header {
    padding: 12px 16px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px;
    font-weight: 600; font-size: 14px;
  }
  .record-header .emoji { font-size: 16px; }
  .record-body {
    padding: 16px; font-size: 14px; line-height: 1.7;
    white-space: pre-wrap; word-break: break-word;
  }
  .record-body code {
    background: var(--bg); padding: 2px 6px; border-radius: 4px;
    font-size: 13px; font-family: 'SF Mono', Monaco, Consolas, monospace;
  }
  .record-tags { padding: 8px 16px; border-top: 1px solid var(--border); }
  .tag {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 12px; background: #1f2a3d; color: var(--accent);
    margin-right: 4px;
  }

  .record-actions {
    padding: 8px 16px; border-top: 1px solid var(--border);
    display: flex; gap: 8px; flex-wrap: wrap;
  }
  .btn-action {
    background: transparent; border: 1px solid var(--border);
    padding: 4px 10px; border-radius: 4px; font-size: 12px;
    cursor: pointer; transition: background 0.15s;
    color: var(--text2);
  }
  .btn-action:hover { background: var(--surface2); color: var(--text); }
  .btn-action.btn-delete { color: var(--danger); }
  .btn-action.btn-delete:hover { background: rgba(248, 81, 73, 0.1); color: var(--danger); }

  .btn-delete-session {
    background: transparent; border: 1px solid var(--border);
    color: var(--danger); padding: 6px 12px; border-radius: var(--radius);
    font-size: 13px; cursor: pointer; transition: background 0.15s;
  }
  .btn-delete-session:hover { background: rgba(248, 81, 73, 0.1); }

  /* Stats */
  .stats-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px; margin-bottom: 24px;
  }
  .stat-card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 20px;
  }
  .stat-card .label { font-size: 13px; color: var(--text2); margin-bottom: 4px; }
  .stat-card .value { font-size: 28px; font-weight: 700; }
  .stat-card .value.accent { color: var(--accent); }
  .stat-card .value.green { color: var(--accent2); }
  .stat-card .value.purple { color: var(--purple); }
  .stat-card .value.warn { color: var(--warn); }

  .chart-bar-container { margin-bottom: 24px; }
  .chart-bar-row {
    display: flex; align-items: center; gap: 12px; margin-bottom: 8px;
  }
  .chart-bar-label {
    width: 160px; font-size: 13px; color: var(--text2); text-align: right;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .chart-bar-track { flex: 1; height: 24px; background: var(--bg); border-radius: 4px; overflow: hidden; }
  .chart-bar-fill {
    height: 100%; background: var(--accent); border-radius: 4px;
    display: flex; align-items: center; padding-left: 8px;
    font-size: 12px; font-weight: 600; min-width: fit-content;
    transition: width 0.3s ease;
  }

  /* Tabs */
  .tabs {
    display: flex; gap: 4px; padding: 0 16px; background: var(--surface);
    border-bottom: 1px solid var(--border);
  }
  .tab {
    padding: 10px 16px; font-size: 13px; color: var(--text2);
    cursor: pointer; border-bottom: 2px solid transparent;
    transition: all 0.15s;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  ::-webkit-scrollbar { width: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text2); }

  /* Modal */
  .modal-overlay {
    position: fixed; inset: 0; background: rgba(0, 0, 0, 0.7);
    display: none; align-items: center; justify-content: center;
    z-index: 500;
  }
  .modal-overlay.show { display: flex; }
  .modal-content {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 24px;
    min-width: 420px; max-width: 560px;
  }
  .modal-title {
    font-size: 16px; font-weight: 600; margin-bottom: 16px;
    display: flex; align-items: center; gap: 8px;
  }
  .modal-close {
    margin-left: auto; background: transparent; border: none; color: var(--text2);
    font-size: 20px; cursor: pointer; line-height: 1;
  }
  .modal-close:hover { color: var(--text); }
  .form-group { margin-bottom: 12px; }
  .form-group label {
    display: block; font-size: 12px; color: var(--text2); margin-bottom: 4px;
  }
  .form-group select,
  .form-group input {
    width: 100%; background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 8px 10px; font-size: 13px; outline: none;
  }
  .form-group select:focus,
  .form-group input:focus { border-color: var(--accent); }
  .modal-hint {
    font-size: 12px; color: var(--text2); margin-bottom: 12px;
    padding: 8px 10px; background: var(--bg); border-radius: 4px;
  }
  .modal-preview {
    font-size: 13px; color: var(--accent); margin: 12px 0;
    padding: 10px 12px; background: var(--bg); border-radius: 4px;
    border: 1px solid var(--border);
  }
  .modal-actions {
    display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px;
  }
  .btn-primary,
  .btn-secondary,
  .btn-danger {
    padding: 8px 14px; border-radius: var(--radius); font-size: 13px;
    cursor: pointer; border: 1px solid var(--border); transition: all 0.15s;
  }
  .btn-secondary { background: transparent; color: var(--text2); }
  .btn-secondary:hover { background: var(--surface2); color: var(--text); }
  .btn-primary { background: transparent; color: var(--accent); border-color: var(--accent); }
  .btn-primary:hover { background: rgba(88, 166, 255, 0.1); }
  .btn-danger { background: transparent; color: var(--danger); border-color: var(--danger); }
  .btn-danger:hover { background: rgba(248, 81, 73, 0.1); }
  .btn-danger:disabled,
  .btn-primary:disabled { opacity: 0.4; cursor: not-allowed; }

  /* Toast */
  .toast-container {
    position: fixed; bottom: 20px; right: 20px;
    display: flex; flex-direction: column; gap: 8px;
    z-index: 1000;
  }
  .toast {
    background: var(--surface); border: 1px solid var(--accent2);
    color: var(--text); padding: 10px 14px; border-radius: var(--radius);
    font-size: 13px; min-width: 200px; max-width: 360px;
    animation: toast-in 0.2s ease;
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
  }
  .toast.error { border-color: var(--danger); }
  .toast.warn { border-color: var(--warn); }
  .toast.fade-out { animation: toast-out 0.3s ease forwards; }
  @keyframes toast-in {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  @keyframes toast-out {
    to { opacity: 0; transform: translateY(10px); }
  }

  @media (max-width: 768px) {
    .main { flex-direction: column; }
    .sidebar { width: 100%; min-width: unset; max-height: 40vh; }
    .header-sub { display: none; }
  }
</style>
</head>
<body>

<div class="header">
  <h1><span>&#9881;</span> <span id="headerTitle">Context Save Viewer</span></h1>
  <div class="header-sub" id="headerSub"></div>
  <input class="search-box" type="text" placeholder="搜尋關鍵字..." id="searchInput">
  <div class="header-stats" id="headerStats"></div>
  <button class="btn-batch-delete" id="btnOpenBatchDelete">&#128465; 批次刪除</button>
</div>

<div class="tabs">
  <div class="tab active" data-tab="sessions">Sessions</div>
  <div class="tab" data-tab="stats">Stats</div>
</div>

<div class="main">
  <div class="sidebar" id="sidebarPanel">
    <div class="sidebar-header">
      <div class="sidebar-header-row">
        <span id="sessionCount">0 sessions</span>
      </div>
      <div class="sidebar-header-row">
        <label for="projectFilter" style="font-size: 12px;">專案</label>
        <select id="projectFilter" style="flex: 1;">
          <option value="">全部</option>
        </select>
      </div>
      <div class="sidebar-header-row">
        <select id="categoryFilter">
          <option value="">全部類別</option>
        </select>
        <select id="sortOrder">
          <option value="newest">最新優先</option>
          <option value="oldest">最舊優先</option>
          <option value="ctx-high">Context% 高→低</option>
        </select>
      </div>
    </div>
    <div class="session-list" id="sessionList"></div>
  </div>
  <div class="content" id="contentPanel">
    <div class="empty-state">
      <div class="icon">&#128203;</div>
      <div>選擇左側的 session 查看詳情</div>
      <div style="font-size: 13px;">或切換到 Stats 頁籤查看統計</div>
    </div>
  </div>
</div>

<!-- 批次刪除 Modal -->
<div class="modal-overlay" id="batchDeleteModal">
  <div class="modal-content" id="batchDeleteContent">
    <div class="modal-title">
      <span>&#128465;</span>
      <span>批次刪除記錄</span>
      <button class="modal-close" id="btnBatchClose">&times;</button>
    </div>
    <div class="modal-hint">至少指定一個條件（分類 / 專案 / 早於 / 晚於）才可執行刪除。</div>
    <div class="form-group">
      <label>分類</label>
      <select id="batchCategory">
        <option value="">（全部）</option>
      </select>
    </div>
    <div class="form-group">
      <label>專案</label>
      <select id="batchProject">
        <option value="">（全部）</option>
      </select>
    </div>
    <div class="form-group">
      <label>早於（created_at &lt;）</label>
      <input type="date" id="batchBefore">
    </div>
    <div class="form-group">
      <label>晚於（created_at &gt;=）</label>
      <input type="date" id="batchAfter">
    </div>
    <div class="modal-preview" id="batchPreview" style="display: none;"></div>
    <div class="modal-actions">
      <button class="btn-secondary" id="btnBatchCancel">取消</button>
      <button class="btn-primary" id="btnBatchPreview">預覽筆數</button>
      <button class="btn-danger" id="btnBatchConfirm" disabled>確認刪除</button>
    </div>
  </div>
</div>

<!-- Toast -->
<div class="toast-container" id="toastContainer"></div>

<script>
/* ================================================================
 * Context Save Viewer v2.1 — 前端 JS
 *
 * 安全原則：
 *  - 所有 DB 值透過 textContent / dataset 寫入 DOM，避免 XSS
 *  - 不使用 onclick="fn('${value}')" 字串組；改 addEventListener + dataset
 *  - 批次刪除 preview → confirm 之間以 filter snapshot + HMAC token 雙重保護
 * ================================================================ */

const CATEGORY_MAP = {
  task: { emoji: '📋', label: '任務進度' },
  finding: { emoji: '🔍', label: '發現與結論' },
  decision: { emoji: '🏗️', label: '架構決策' },
  bug: { emoji: '🐛', label: 'Bug 追蹤' },
  change: { emoji: '📝', label: '程式碼變更' },
  insight: { emoji: '💡', label: '重要洞察' },
  reference: { emoji: '🔗', label: '外部資源' },
  custom: { emoji: '📌', label: '自訂' },
};

let allSessions = [];
let allRecords = {};
let currentSessionId = null;
let currentProjectFilter = '';

// Batch delete snapshot + HMAC token
let batchConfirmFilter = null;   // JSON.stringify(filter) at preview time
let batchConfirmToken = null;    // HMAC token from server
let batchPreviewCount = null;

/* ---------------- 共用工具 ---------------- */

function escapeHtml(text) {
  // 保留給少數不得已用 innerHTML 的場景；這份檔案盡量不走這路
  if (text == null) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeJs(value) {
  // JS 字面值注入防護；JSON.stringify 會包含引號與 escape
  return JSON.stringify(value == null ? '' : String(value));
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return '';
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function ctxBadgeClass(pct) {
  if (pct >= 70) return 'ctx-high';
  if (pct >= 50) return 'ctx-mid';
  return 'ctx-low';
}

function homeRelative(path) {
  // 伺服器已 normalize，但保留前端的二次美化（容錯）
  return path || '(unknown)';
}

async function fetchJSON(url, opts) {
  const options = opts || {};
  let res;
  try {
    res = await fetch(url, options);
  } catch (err) {
    return { status: 0, data: { ok: false, error: 'network error: ' + (err && err.message) } };
  }
  let data;
  try {
    data = await res.json();
  } catch (_e) {
    data = { ok: false, error: 'invalid JSON response' };
  }
  return { status: res.status, data };
}

async function fetchAPI(endpoint, query) {
  let url = '/api/' + endpoint;
  if (query) {
    const params = new URLSearchParams();
    Object.keys(query).forEach(k => {
      const v = query[k];
      if (v != null && v !== '') params.set(k, v);
    });
    const qs = params.toString();
    if (qs) url += '?' + qs;
  }
  const { status, data } = await fetchJSON(url);
  if (status === 503) {
    showToast('資料庫暫時忙碌，請稍候重試', 'warn');
  }
  return data;
}

/* ---------------- Toast ---------------- */

function showToast(msg, type) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  const cls = type === 'error' ? ' error' : type === 'warn' ? ' warn' : '';
  toast.className = 'toast' + cls;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => toast.remove(), 300);
  }, 2200);
}

/* ---------------- Header ---------------- */

async function updateHeader() {
  const data = await fetchAPI('ping');
  if (!data || !data.ok) return;
  const title = document.getElementById('headerTitle');
  title.textContent = 'Context Save Viewer';
  const sub = document.getElementById('headerSub');
  const dbPath = data.db_path || '(unknown)';
  const version = data.version || '';
  sub.textContent = `DB: ${dbPath} — v${version}`;
  sub.title = `DB: ${dbPath} — v${version}`;
}

/* ---------------- Projects ---------------- */

async function loadProjects() {
  const data = await fetchAPI('projects');
  const projects = (data && data.projects) || [];
  const select = document.getElementById('projectFilter');
  const batchSelect = document.getElementById('batchProject');

  // 清空並重建（除了第一個「全部」選項）
  while (select.options.length > 1) select.remove(1);
  while (batchSelect.options.length > 1) batchSelect.remove(1);

  projects.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p.path || '';
    const label = `${homeRelative(p.path)} (${p.record_count || 0})`;
    opt.textContent = label;
    select.appendChild(opt);

    const bopt = document.createElement('option');
    bopt.value = p.path || '';
    bopt.textContent = label;
    batchSelect.appendChild(bopt);
  });
}

function applyProjectFilter(path) {
  currentProjectFilter = path || '';
  loadSessions();
}

/* ---------------- Sessions 載入與渲染 ---------------- */

async function loadSessions() {
  const data = await fetchAPI('sessions', { project_path: currentProjectFilter });
  allSessions = (data && data.sessions) || [];
  allRecords = {};

  const categories = new Set();
  allSessions.forEach(s => {
    (s.categories || '').split(',').forEach(c => {
      const t = c.trim();
      if (t) categories.add(t);
    });
  });

  const select = document.getElementById('categoryFilter');
  while (select.options.length > 1) select.remove(1);
  categories.forEach(c => {
    const cat = CATEGORY_MAP[c] || { emoji: '📌', label: c };
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = `${cat.emoji} ${cat.label}`;
    select.appendChild(opt);
  });

  const stats = document.getElementById('headerStats');
  stats.innerHTML = ''; // 無 user input，此處為安全常數
  const s1 = document.createElement('span');
  s1.textContent = 'Sessions: ';
  const b1 = document.createElement('strong');
  b1.textContent = String(allSessions.length);
  s1.appendChild(b1);
  const s2 = document.createElement('span');
  s2.textContent = 'Records: ';
  const b2 = document.createElement('strong');
  b2.textContent = String((data && data.total_records) || 0);
  s2.appendChild(b2);
  stats.appendChild(s1);
  stats.appendChild(s2);

  filterSessions();
}

function renderSessionRow(session) {
  const row = document.createElement('div');
  row.className = 'session-item';
  row.dataset.sessionId = session.session_id || '';
  row.addEventListener('click', (e) => {
    const sid = e.currentTarget.dataset.sessionId;
    if (sid) selectSession(sid);
  });

  const dateEl = document.createElement('div');
  dateEl.className = 'session-date';
  dateEl.textContent = formatDate(session.created_at);
  row.appendChild(dateEl);

  const titleEl = document.createElement('div');
  titleEl.className = 'session-title';
  const titleText = (session.titles || '').split(' | ')[0]
                    || (session.session_id || '').slice(0, 12);
  titleEl.textContent = titleText;
  row.appendChild(titleEl);

  if (session.project_path) {
    const projEl = document.createElement('div');
    projEl.className = 'session-project';
    projEl.textContent = '📁 ' + homeRelative(session.project_path);
    projEl.title = session.project_path;
    row.appendChild(projEl);
  }

  const metaEl = document.createElement('div');
  metaEl.className = 'session-meta';
  const cats = (session.categories || '').split(',').filter(Boolean);
  cats.forEach(c => metaEl.appendChild(renderCategory(c.trim())));

  const ctxPct = session.context_percentage || 0;
  const ctxBadge = document.createElement('span');
  ctxBadge.className = 'ctx-badge ' + ctxBadgeClass(ctxPct);
  ctxBadge.textContent = ctxPct + '%';
  metaEl.appendChild(ctxBadge);

  const countBadge = document.createElement('span');
  countBadge.className = 'category-badge';
  countBadge.textContent = (session.item_count || 0) + ' 項';
  metaEl.appendChild(countBadge);

  row.appendChild(metaEl);
  return row;
}

function renderCategory(cat) {
  const info = CATEGORY_MAP[cat] || { emoji: '📌', label: cat };
  const span = document.createElement('span');
  span.className = 'category-badge';
  span.textContent = `${info.emoji} ${info.label}`;
  return span;
}

function filterSessions() {
  const keyword = document.getElementById('searchInput').value.toLowerCase();
  const category = document.getElementById('categoryFilter').value;
  const sort = document.getElementById('sortOrder').value;

  let filtered = allSessions.filter(s => {
    if (category && !(s.categories || '').includes(category)) return false;
    if (keyword) {
      const t = (s.titles || '').toLowerCase();
      const sid = (s.session_id || '').toLowerCase();
      if (!t.includes(keyword) && !sid.includes(keyword)) return false;
    }
    return true;
  });

  if (sort === 'oldest') {
    filtered.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
  } else if (sort === 'ctx-high') {
    filtered.sort((a, b) => (b.context_percentage || 0) - (a.context_percentage || 0));
  } else {
    filtered.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  }

  document.getElementById('sessionCount').textContent = `${filtered.length} sessions`;

  const list = document.getElementById('sessionList');
  list.innerHTML = '';
  filtered.forEach(s => list.appendChild(renderSessionRow(s)));
}

async function selectSession(sessionId) {
  currentSessionId = sessionId;
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.sessionId === sessionId);
  });

  let records = allRecords[sessionId];
  if (!records) {
    const data = await fetchAPI('session/' + encodeURIComponent(sessionId));
    records = (data && data.records) || [];
    allRecords[sessionId] = records;
  }
  renderSessionDetail(sessionId, records);
}

function renderSessionDetail(sessionId, records) {
  const content = document.getElementById('contentPanel');
  content.innerHTML = '';

  if (!records.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    const icon = document.createElement('div');
    icon.className = 'icon';
    icon.innerHTML = '&#128196;';
    const msg = document.createElement('div');
    msg.textContent = '此 session 無記錄';
    empty.appendChild(icon);
    empty.appendChild(msg);
    content.appendChild(empty);
    return;
  }

  const first = records[0];

  const header = document.createElement('div');
  header.className = 'detail-header';

  const headerTop = document.createElement('div');
  headerTop.className = 'detail-header-top';
  const h2 = document.createElement('h2');
  h2.textContent = first.title || sessionId;
  headerTop.appendChild(h2);

  const delBtn = document.createElement('button');
  delBtn.className = 'btn-delete-session';
  delBtn.innerHTML = '&#128465; 刪除整個 session';
  delBtn.dataset.sessionId = sessionId;
  delBtn.addEventListener('click', (e) => {
    deleteSession(e.currentTarget.dataset.sessionId);
  });
  headerTop.appendChild(delBtn);
  header.appendChild(headerTop);

  const meta = document.createElement('div');
  meta.className = 'detail-meta';
  const metaParts = [
    '📅 ' + formatDate(first.created_at),
    '🤖 ' + (first.model || 'N/A'),
    '📊 Context: ' + (first.context_percentage || 0) + '%',
    '📋 ' + records.length + ' 項記錄',
  ];
  if (first.project_path) {
    metaParts.push('📁 ' + homeRelative(first.project_path));
  }
  metaParts.forEach(t => {
    const span = document.createElement('span');
    span.textContent = t;
    meta.appendChild(span);
  });
  header.appendChild(meta);
  content.appendChild(header);

  records.forEach(r => content.appendChild(renderRecordCard(r)));
}

function renderRecordCard(r) {
  const cat = CATEGORY_MAP[r.category] || { emoji: '📌', label: r.category || '' };
  const card = document.createElement('div');
  card.className = 'record-card';
  card.dataset.recordId = String(r.id);

  const hdr = document.createElement('div');
  hdr.className = 'record-header';
  const emo = document.createElement('span');
  emo.className = 'emoji';
  emo.textContent = cat.emoji;
  hdr.appendChild(emo);
  const lbl = document.createElement('span');
  lbl.textContent = cat.label;
  hdr.appendChild(lbl);
  const sub = document.createElement('span');
  sub.style.marginLeft = 'auto';
  sub.style.fontWeight = '400';
  sub.style.fontSize = '12px';
  sub.style.color = 'var(--text2)';
  sub.textContent = r.title || '';
  hdr.appendChild(sub);
  card.appendChild(hdr);

  const body = document.createElement('div');
  body.className = 'record-body';
  body.textContent = r.content || '';
  card.appendChild(body);

  const tagsStr = r.tags || '';
  const tagList = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
  if (tagList.length) {
    const tagsBox = document.createElement('div');
    tagsBox.className = 'record-tags';
    tagList.forEach(t => {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = t;
      tagsBox.appendChild(tag);
    });
    card.appendChild(tagsBox);
  }

  const actions = document.createElement('div');
  actions.className = 'record-actions';

  const btnDel = document.createElement('button');
  btnDel.className = 'btn-action btn-delete';
  btnDel.innerHTML = '&#128465; 刪除';
  btnDel.dataset.recordId = String(r.id);
  btnDel.addEventListener('click', (e) => {
    const id = parseInt(e.currentTarget.dataset.recordId, 10);
    if (Number.isInteger(id)) deleteRecord(id);
  });
  actions.appendChild(btnDel);

  const btnCopy = document.createElement('button');
  btnCopy.className = 'btn-action';
  btnCopy.innerHTML = '&#128203; 複製內容';
  btnCopy.dataset.recordId = String(r.id);
  btnCopy.addEventListener('click', (e) => {
    const id = parseInt(e.currentTarget.dataset.recordId, 10);
    if (Number.isInteger(id)) copyContent(id);
  });
  actions.appendChild(btnCopy);

  const btnMd = document.createElement('button');
  btnMd.className = 'btn-action';
  btnMd.innerHTML = '&#128203; 複製 MD';
  btnMd.dataset.recordId = String(r.id);
  btnMd.addEventListener('click', (e) => {
    const id = parseInt(e.currentTarget.dataset.recordId, 10);
    if (Number.isInteger(id)) copyMarkdown(id);
  });
  actions.appendChild(btnMd);

  card.appendChild(actions);
  return card;
}

/* ---------------- 刪除操作 ---------------- */

async function deleteRecord(id) {
  if (!confirm('確定刪除此筆記錄？')) return;
  const { status, data } = await fetchJSON(`/api/saves/${id}`, { method: 'DELETE' });
  if (status === 200 && data.ok) {
    const card = document.querySelector(`.record-card[data-record-id="${CSS.escape(String(id))}"]`);
    if (card) card.remove();
    if (currentSessionId && allRecords[currentSessionId]) {
      allRecords[currentSessionId] = allRecords[currentSessionId].filter(r => r.id !== id);
    }
    const modeMsg = data.mode === 'soft' ? '已軟刪除（DB 忙碌中）' : '已刪除';
    showToast(modeMsg, data.mode === 'soft' ? 'warn' : 'success');
  } else if (status === 404) {
    showToast('記錄不存在', 'error');
  } else if (status === 403) {
    showToast('跨來源請求遭阻擋', 'error');
  } else if (status === 503) {
    showToast('資料庫暫時忙碌，請稍候重試', 'warn');
  } else {
    showToast('刪除失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

async function deleteSession(sessionId) {
  if (!confirm(`確定刪除整個 session 的所有記錄？\n\nSession ID: ${sessionId}`)) return;
  const { status, data } = await fetchJSON(
    `/api/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }
  );
  if (status === 200 && data.ok) {
    const modeMsg = data.mode === 'soft' ? '已軟刪除' : '已刪除';
    showToast(`${modeMsg} ${data.affected} 筆記錄`, data.mode === 'soft' ? 'warn' : 'success');
    await loadProjects();
    await loadSessions();
    currentSessionId = null;
    resetContentPanel();
  } else if (status === 404) {
    showToast('Session 不存在', 'error');
  } else if (status === 400) {
    showToast('Session ID 格式不合法', 'error');
  } else if (status === 403) {
    showToast('跨來源請求遭阻擋', 'error');
  } else if (status === 503) {
    showToast('資料庫暫時忙碌，請稍候重試', 'warn');
  } else {
    showToast('刪除失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

/* ---------------- 複製操作 ---------------- */

function findRecordById(id) {
  for (const sid in allRecords) {
    const r = allRecords[sid].find(rec => rec.id === id);
    if (r) return r;
  }
  return null;
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_e) {
    try {
      prompt('請手動複製（navigator.clipboard 無法使用）:', text);
      return true;
    } catch (_e2) {
      return false;
    }
  }
}

async function copyContent(id) {
  const r = findRecordById(id);
  if (!r) { showToast('找不到記錄', 'error'); return; }
  const ok = await copyToClipboard(r.content || '');
  showToast(ok ? '已複製內容' : '複製失敗', ok ? 'success' : 'error');
}

async function copyMarkdown(id) {
  const r = findRecordById(id);
  if (!r) { showToast('找不到記錄', 'error'); return; }
  const md = [
    '---',
    `title: ${r.title || ''}`,
    `category: ${r.category || ''}`,
    `tags: ${r.tags || ''}`,
    `context_percentage: ${r.context_percentage || 0}%`,
    `session_id: ${r.session_id || currentSessionId || ''}`,
    `project_path: ${r.project_path || ''}`,
    `created_at: ${r.created_at || ''}`,
    `model: ${r.model || ''}`,
    '---',
    '',
    r.content || '',
  ].join('\n');
  const ok = await copyToClipboard(md);
  showToast(ok ? '已複製 Markdown' : '複製失敗', ok ? 'success' : 'error');
}

/* ---------------- 批次刪除 Modal ---------------- */

async function openBatchDeleteModal() {
  const stats = await fetchAPI('stats');
  const select = document.getElementById('batchCategory');
  while (select.options.length > 1) select.remove(1);
  const cats = Object.keys((stats && stats.categories) || {});
  cats.forEach(c => {
    const cat = CATEGORY_MAP[c] || { emoji: '📌', label: c };
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = `${cat.emoji} ${cat.label} (${stats.categories[c]})`;
    select.appendChild(opt);
  });
  // 專案下拉已在 loadProjects 填好，此處不重建

  document.getElementById('batchBefore').value = '';
  document.getElementById('batchAfter').value = '';
  document.getElementById('batchPreview').style.display = 'none';
  document.getElementById('btnBatchConfirm').disabled = true;

  batchPreviewCount = null;
  batchConfirmFilter = null;
  batchConfirmToken = null;

  document.getElementById('batchDeleteModal').classList.add('show');
}

function closeBatchDeleteModal() {
  document.getElementById('batchDeleteModal').classList.remove('show');
}

function collectBatchFilter() {
  const category = document.getElementById('batchCategory').value || null;
  const projectPath = document.getElementById('batchProject').value || null;
  const beforeDate = document.getElementById('batchBefore').value;
  const afterDate = document.getElementById('batchAfter').value;
  // 與 SQLite CURRENT_TIMESTAMP 格式（'YYYY-MM-DD HH:MM:SS'）對齊，
  // 避免字典序誤差（ISO 'T'/'Z' 與空白比較會讓 23:59:59 被誤歸入次日）。
  const before = beforeDate ? `${beforeDate} 00:00:00` : null;
  const after = afterDate ? `${afterDate} 00:00:00` : null;
  // 欄位順序不影響，但保留穩定結構供 JSON.stringify 比對
  const f = {};
  if (category) f.category = category;
  if (projectPath) f.project_path = projectPath;
  if (before) f.before = before;
  if (after) f.after = after;
  return f;
}

async function previewBatchDelete() {
  const filter = collectBatchFilter();
  if (!Object.keys(filter).length) {
    showToast('請至少指定一個條件', 'error');
    return;
  }
  const { status, data } = await fetchJSON('/api/saves/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ dry_run: true, filter: filter }),
  });
  const preview = document.getElementById('batchPreview');
  if (status === 200 && data.ok) {
    batchPreviewCount = data.affected || 0;
    batchConfirmFilter = JSON.stringify(filter);
    batchConfirmToken = data.confirm_token || null;
    preview.textContent = `將刪除 ${batchPreviewCount} 筆記錄`;
    preview.style.display = 'block';
    document.getElementById('btnBatchConfirm').disabled =
      (batchPreviewCount === 0) || !batchConfirmToken;
  } else {
    preview.style.display = 'none';
    batchPreviewCount = null;
    batchConfirmFilter = null;
    batchConfirmToken = null;
    document.getElementById('btnBatchConfirm').disabled = true;
    if (status === 403) showToast('跨來源請求遭阻擋', 'error');
    else if (status === 413) showToast('請求過大', 'error');
    else if (status === 503) showToast('資料庫暫時忙碌，請稍候重試', 'warn');
    else showToast('預覽失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

async function confirmBatchDelete() {
  if (batchPreviewCount == null || !batchConfirmToken) {
    showToast('請先預覽筆數', 'error');
    return;
  }
  const currentFilter = collectBatchFilter();
  if (JSON.stringify(currentFilter) !== batchConfirmFilter) {
    showToast('條件已變更，請重新預覽', 'warn');
    document.getElementById('btnBatchConfirm').disabled = true;
    document.getElementById('batchPreview').style.display = 'none';
    batchPreviewCount = null;
    batchConfirmFilter = null;
    batchConfirmToken = null;
    return;
  }
  if (!confirm(`確定刪除 ${batchPreviewCount} 筆記錄？此動作無法復原。`)) return;

  const { status, data } = await fetchJSON('/api/saves/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      dry_run: false,
      filter: currentFilter,
      confirm_token: batchConfirmToken,
    }),
  });
  if (status === 200 && data.ok) {
    const modeMsg = data.mode === 'soft' ? '已軟刪除' : '已刪除';
    const batches = data.batches != null ? ` (${data.batches} 批)` : '';
    showToast(`${modeMsg} ${data.affected} 筆記錄${batches}`,
              data.mode === 'soft' ? 'warn' : 'success');
    closeBatchDeleteModal();
    await loadProjects();
    await loadSessions();
    currentSessionId = null;
    resetContentPanel();
  } else if (status === 400) {
    showToast(data.error || '條件已變更，請重新預覽', 'warn');
    document.getElementById('btnBatchConfirm').disabled = true;
    batchConfirmFilter = null;
    batchConfirmToken = null;
  } else if (status === 403) {
    showToast('跨來源請求遭阻擋', 'error');
  } else if (status === 413) {
    showToast('請求過大', 'error');
  } else if (status === 503) {
    showToast('資料庫暫時忙碌，請稍候重試', 'warn');
  } else {
    showToast('刪除失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

/* ---------------- Stats ---------------- */

async function renderStats() {
  const data = await fetchAPI('stats');
  const content = document.getElementById('contentPanel');
  content.innerHTML = '';

  const grid = document.createElement('div');
  grid.className = 'stats-grid';
  const cards = [
    ['總記錄數', data.total_records || 0, 'accent'],
    ['總 Session 數', data.total_sessions || 0, 'green'],
    ['類別數', Object.keys(data.categories || {}).length, 'purple'],
    ['專案數', Object.keys(data.by_project || {}).length, 'warn'],
  ];
  cards.forEach(([label, value, cls]) => {
    const card = document.createElement('div');
    card.className = 'stat-card';
    const lbl = document.createElement('div');
    lbl.className = 'label';
    lbl.textContent = label;
    const val = document.createElement('div');
    val.className = 'value ' + cls;
    val.textContent = String(value);
    card.appendChild(lbl);
    card.appendChild(val);
    grid.appendChild(card);
  });

  const rangeCard = document.createElement('div');
  rangeCard.className = 'stat-card';
  const rl = document.createElement('div');
  rl.className = 'label';
  rl.textContent = '時間範圍';
  const rv = document.createElement('div');
  rv.className = 'value warn';
  rv.style.fontSize = '16px';
  const oldest = data.oldest ? formatDate(data.oldest).split(' ')[0] : 'N/A';
  const newest = data.newest ? formatDate(data.newest).split(' ')[0] : 'N/A';
  rv.textContent = `${oldest} ~ ${newest}`;
  rangeCard.appendChild(rl);
  rangeCard.appendChild(rv);
  grid.appendChild(rangeCard);

  content.appendChild(grid);

  content.appendChild(renderBarSection('各類別分布',
    Object.entries(data.categories || {}).map(([k, v]) => {
      const c = CATEGORY_MAP[k] || { emoji: '📌', label: k };
      return [`${c.emoji} ${c.label}`, v];
    })));

  content.appendChild(renderBarSection('各專案分布',
    Object.entries(data.by_project || {}).map(([k, v]) => [homeRelative(k), v])));
}

function renderBarSection(title, entries) {
  const wrap = document.createElement('div');
  const h = document.createElement('h3');
  h.style.marginBottom = '16px';
  h.textContent = title;
  wrap.appendChild(h);

  const container = document.createElement('div');
  container.className = 'chart-bar-container';

  if (!entries.length) {
    const empty = document.createElement('div');
    empty.style.color = 'var(--text2)';
    empty.textContent = '尚無資料';
    container.appendChild(empty);
    wrap.appendChild(container);
    return wrap;
  }

  const maxCount = Math.max.apply(null, entries.map(e => e[1])) || 1;
  entries.sort((a, b) => b[1] - a[1]).forEach(([label, count]) => {
    const row = document.createElement('div');
    row.className = 'chart-bar-row';
    const lbl = document.createElement('div');
    lbl.className = 'chart-bar-label';
    lbl.textContent = label;
    lbl.title = label;
    const track = document.createElement('div');
    track.className = 'chart-bar-track';
    const fill = document.createElement('div');
    fill.className = 'chart-bar-fill';
    const pct = (count / maxCount * 100).toFixed(0);
    fill.style.width = pct + '%';
    fill.textContent = String(count);
    track.appendChild(fill);
    row.appendChild(lbl);
    row.appendChild(track);
    container.appendChild(row);
  });
  wrap.appendChild(container);
  return wrap;
}

/* ---------------- Tabs / 版面 ---------------- */

function resetContentPanel() {
  const content = document.getElementById('contentPanel');
  content.innerHTML = '';
  const empty = document.createElement('div');
  empty.className = 'empty-state';
  const icon = document.createElement('div');
  icon.className = 'icon';
  icon.innerHTML = '&#128203;';
  const msg = document.createElement('div');
  msg.textContent = '選擇左側的 session 查看詳情';
  empty.appendChild(icon);
  empty.appendChild(msg);
  content.appendChild(empty);
}

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  const sidebar = document.getElementById('sidebarPanel');
  if (tab === 'stats') {
    sidebar.style.display = 'none';
    renderStats();
  } else {
    sidebar.style.display = '';
    resetContentPanel();
  }
}

/* ---------------- 事件綁定 ---------------- */

document.addEventListener('DOMContentLoaded', () => {
  // Tabs
  document.querySelectorAll('.tab').forEach(t => {
    t.addEventListener('click', () => switchTab(t.dataset.tab));
  });

  // Header
  document.getElementById('btnOpenBatchDelete').addEventListener('click', openBatchDeleteModal);

  // Sidebar filters
  document.getElementById('projectFilter').addEventListener('change', (e) => {
    applyProjectFilter(e.currentTarget.value);
  });
  document.getElementById('categoryFilter').addEventListener('change', filterSessions);
  document.getElementById('sortOrder').addEventListener('change', filterSessions);

  // Search (debounce)
  document.getElementById('searchInput').addEventListener('input', () => {
    clearTimeout(window._searchTimer);
    window._searchTimer = setTimeout(filterSessions, 200);
  });

  // Modal buttons
  document.getElementById('btnBatchClose').addEventListener('click', closeBatchDeleteModal);
  document.getElementById('btnBatchCancel').addEventListener('click', closeBatchDeleteModal);
  document.getElementById('btnBatchPreview').addEventListener('click', previewBatchDelete);
  document.getElementById('btnBatchConfirm').addEventListener('click', confirmBatchDelete);
  document.getElementById('batchDeleteModal').addEventListener('click', (e) => {
    if (e.target.id === 'batchDeleteModal') closeBatchDeleteModal();
  });
  // filter 改動時重置 confirm 狀態（snapshot 一致性由 confirmBatchDelete 再次驗證）
  ['batchCategory', 'batchProject', 'batchBefore', 'batchAfter'].forEach(id => {
    document.getElementById(id).addEventListener('change', () => {
      document.getElementById('btnBatchConfirm').disabled = true;
    });
  });

  // 啟動流程
  (async () => {
    await updateHeader();
    await loadProjects();
    await loadSessions();
  })();
});
</script>
</body>
</html>"""


# --------------------------------------------------------------------------
# RequestHandler
# --------------------------------------------------------------------------

class RequestHandler(BaseHTTPRequestHandler):
    """HTTP 路由 + CSRF + HMAC 二次確認 + DB 讀寫

    設計模式參考 arch.md §4：
      - Template Method: do_POST / do_DELETE 共用 CSRF → body → OperationalError 前處理
      - Guard Clause:    _reject_cross_origin / _read_json_body 失敗時寫 response 後早退
      - Factory Method:  get_db(writer=...) 包裝 ctx-db 連線工廠
    """

    # 讓 log 使用自訂 logger（避免 BaseHTTPRequestHandler 預設 stderr 格式）
    def log_message(self, format, *args):
        try:
            line = format % args
        except Exception:
            line = " ".join(str(a) for a in args)
        logger.info(line)

    # --------- 基礎回應 ---------

    def _json_response(self, status, payload):
        """統一 JSON 回應寫法"""
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # client 已斷線
            pass

    def _send_html(self, html):
        body = html.encode("utf-8")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _not_found(self):
        self._json_response(404, {"ok": False, "error": "not found"})

    # --------- CSRF ---------

    def _host_from(self, url):
        """從 URL 取 hostname；空值回 None"""
        if not url:
            return None
        try:
            return urlparse(url).hostname
        except Exception:
            return None

    def _reject_cross_origin(self):
        """CSRF 檢查：Origin 優先 → Referer；不合法寫 403 回 True（= 已拒絕）"""
        origin = self.headers.get("Origin")
        referer = self.headers.get("Referer")
        host = self._host_from(origin) if origin else self._host_from(referer)
        if not host or host not in ALLOWED_ORIGINS:
            logger.warning("cross-origin from origin=%r referer=%r", origin, referer)
            self._json_response(403, {"ok": False, "error": "cross-origin request blocked"})
            return True
        return False

    # --------- Body / JSON ---------

    def _read_json_body(self, max_bytes=MAX_BODY_BYTES):
        """讀 POST body（上限 max_bytes）並 parse JSON。
        失敗時寫 400/413 回應並回傳 None。
        """
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._json_response(400, {"ok": False, "error": "invalid Content-Length"})
            return None
        if length < 0:
            self._json_response(400, {"ok": False, "error": "invalid Content-Length"})
            return None
        if length > max_bytes:
            logger.warning("oversize body %d bytes (max %d)", length, max_bytes)
            self._json_response(413, {"ok": False, "error": "payload too large"})
            return None
        try:
            raw = self.rfile.read(length) if length > 0 else b""
        except Exception as e:
            logger.warning("read body failed: %s", e)
            self._json_response(400, {"ok": False, "error": "cannot read body"})
            return None
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning("invalid JSON: %s", str(e).splitlines()[0])
            self._json_response(400, {"ok": False, "error": "invalid JSON"})
            return None
        if not isinstance(body, dict):
            self._json_response(400, {"ok": False, "error": "body must be a JSON object"})
            return None
        return body

    # --------- HMAC confirm_token ---------

    def _canonical_filter(self, filter_dict):
        """Canonical JSON：sort_keys + 無空白，供 HMAC 計算"""
        return json.dumps(filter_dict or {}, sort_keys=True, separators=(",", ":"))

    def _make_confirm_token(self, filter_dict):
        canonical = self._canonical_filter(filter_dict)
        msg = (canonical + "|" + STARTED_AT).encode("utf-8")
        return hmac.new(CONFIRM_SECRET, msg, hashlib.sha256).hexdigest()

    def _verify_confirm_token(self, token, filter_dict):
        if not token or not isinstance(token, str):
            return False
        expected = self._make_confirm_token(filter_dict)
        try:
            return hmac.compare_digest(token, expected)
        except Exception:
            return False

    # --------- DB / 例外 ---------

    def get_db(self, writer=False):
        """透過 ctx-db 的連線工廠取得 sqlite3.Connection"""
        conn = CTX_DB._connect_db(DB_PATH, writer=writer)
        # 確保有 row_factory（ctx-db 預設已設，這裡保險）
        if conn.row_factory is None:
            conn.row_factory = sqlite3.Row
        return conn

    def _wrap_operational_error(self, exc):
        logger.error("OperationalError: %s\n%s", exc, traceback.format_exc())
        self._json_response(503, {"ok": False, "error": "db busy or readonly"})

    # ============================== GET ==============================

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                self._send_html(HTML_PAGE)
                return
            if path == "/api/ping":
                self._handle_ping()
                return
            if path == "/api/projects":
                self._handle_projects()
                return
            if path == "/api/sessions":
                self._handle_sessions(params)
                return
            if path == "/api/search":
                self._handle_search(params)
                return
            if path == "/api/stats":
                self._handle_stats()
                return
            if path.startswith("/api/session/"):
                session_id = path.split("/api/session/", 1)[1]
                self._handle_session_detail(session_id)
                return
            self._not_found()
        except sqlite3.OperationalError as e:
            self._wrap_operational_error(e)
        except Exception:
            logger.error("unhandled GET error:\n%s", traceback.format_exc())
            self._json_response(500, {"ok": False, "error": "internal error"})

    # ============================== POST ==============================

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/saves/batch-delete":
                if self._reject_cross_origin():
                    return
                self._handle_batch_delete()
                return
            self._not_found()
        except sqlite3.OperationalError as e:
            self._wrap_operational_error(e)
        except Exception:
            logger.error("unhandled POST error:\n%s", traceback.format_exc())
            self._json_response(500, {"ok": False, "error": "internal error"})

    # ============================== DELETE ==============================

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if self._reject_cross_origin():
                return

            if path.startswith("/api/saves/"):
                id_str = path.split("/api/saves/", 1)[1]
                self._handle_delete_save(id_str)
                return
            if path.startswith("/api/session/"):
                session_id = path.split("/api/session/", 1)[1]
                self._handle_delete_session(session_id)
                return
            self._not_found()
        except sqlite3.OperationalError as e:
            self._wrap_operational_error(e)
        except Exception:
            logger.error("unhandled DELETE error:\n%s", traceback.format_exc())
            self._json_response(500, {"ok": False, "error": "internal error"})

    # ============================== Handlers ==============================

    def _handle_ping(self):
        """GET /api/ping — 回傳服務 + DB 統計（供 launcher 與前端 header 使用）"""
        project_count = 0
        record_count = 0
        try:
            with self.get_db() as conn:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT project_path), COUNT(*) "
                    "FROM context_saves WHERE deleted_at IS NULL"
                ).fetchone()
                if row:
                    project_count = int(row[0] or 0)
                    record_count = int(row[1] or 0)
        except sqlite3.OperationalError:
            self._json_response(503, {
                "ok": False,
                "service": SERVICE_NAME,
                "version": VERSION,
                "db_path": _home_relative(DB_PATH),
                "project_count": 0,
                "record_count": 0,
                "started_at": STARTED_AT,
                "error": "db busy or readonly",
            })
            return
        except Exception:
            logger.error("ping stat failed:\n%s", traceback.format_exc())

        self._json_response(200, {
            "ok": True,
            "service": SERVICE_NAME,
            "version": VERSION,
            "db_path": _home_relative(DB_PATH),
            "project_count": project_count,
            "record_count": record_count,
            "started_at": STARTED_AT,
        })

    def _handle_projects(self):
        """GET /api/projects — 回傳專案清單（sidebar 下拉用）"""
        try:
            projects = CTX_DB.list_projects(DB_PATH)
        except sqlite3.OperationalError as e:
            self._wrap_operational_error(e)
            return
        # 每筆含 path / name / record_count / session_count / last_at
        self._json_response(200, {"projects": projects})

    def _handle_sessions(self, params):
        """GET /api/sessions — project_path filter + LIMIT ≤ 500"""
        project_path = (params.get("project_path", [""])[0] or "").strip() or None
        limit_raw = params.get("limit", [str(SESSIONS_LIMIT_MAX)])[0]
        try:
            limit = int(limit_raw)
        except ValueError:
            self._json_response(400, {"ok": False, "error": "invalid limit"})
            return
        if limit < 1 or limit > SESSIONS_LIMIT_MAX:
            self._json_response(400, {"ok": False, "error": "invalid limit"})
            return

        where = ["deleted_at IS NULL"]
        args = []
        if project_path:
            where.append("project_path = ?")
            args.append(project_path)
        where_sql = " AND ".join(where)

        with self.get_db() as conn:
            rows = conn.execute(f"""
                SELECT
                    session_id,
                    MIN(created_at) as created_at,
                    GROUP_CONCAT(DISTINCT category) as categories,
                    MAX(context_percentage) as context_percentage,
                    COUNT(*) as item_count,
                    GROUP_CONCAT(title, ' | ') as titles,
                    project_path
                FROM context_saves
                WHERE {where_sql}
                GROUP BY session_id, project_path
                ORDER BY MIN(created_at) DESC
                LIMIT ?
            """, (*args, limit)).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) FROM context_saves WHERE {where_sql}",
                args,
            ).fetchone()[0]

        sessions = [dict(r) for r in rows]
        self._json_response(200, {"sessions": sessions, "total_records": total})

    def _handle_session_detail(self, session_id):
        """GET /api/session/<id>"""
        if not session_id or not CTX_DB._validate_session_id(session_id):
            self._json_response(400, {"ok": False, "error": "invalid session_id"})
            return
        with self.get_db() as conn:
            rows = conn.execute("""
                SELECT id, session_id, project_path, category, title, content, tags,
                       context_percentage, created_at, model
                FROM context_saves
                WHERE session_id = ? AND deleted_at IS NULL
                ORDER BY id
            """, (session_id,)).fetchall()
        self._json_response(200, {"records": [dict(r) for r in rows]})

    def _handle_search(self, params):
        """GET /api/search — keyword ≤ 200、project_path 選填"""
        keyword = (params.get("q", [""])[0] or "").strip()
        project_path = (params.get("project_path", [""])[0] or "").strip() or None
        if not keyword:
            self._json_response(400, {"ok": False, "error": "q required"})
            return
        if len(keyword) > MAX_SEARCH_LEN:
            self._json_response(400, {
                "ok": False,
                "error": f"q too long (max {MAX_SEARCH_LEN})",
            })
            return

        where = ["(title LIKE ? OR content LIKE ? OR tags LIKE ?)", "deleted_at IS NULL"]
        like = f"%{keyword}%"
        args = [like, like, like]
        if project_path:
            where.append("project_path = ?")
            args.append(project_path)
        where_sql = " AND ".join(where)

        with self.get_db() as conn:
            rows = conn.execute(f"""
                SELECT id, session_id, project_path, created_at, category, title, content
                FROM context_saves
                WHERE {where_sql}
                ORDER BY created_at DESC LIMIT 50
            """, args).fetchall()
        self._json_response(200, {"results": [dict(r) for r in rows]})

    def _handle_stats(self):
        """GET /api/stats — 加 by_project 聚合"""
        with self.get_db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
            sessions = conn.execute(
                "SELECT COUNT(DISTINCT session_id) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
            categories = dict(conn.execute("""
                SELECT category, COUNT(*) FROM context_saves
                WHERE deleted_at IS NULL
                GROUP BY category ORDER BY COUNT(*) DESC
            """).fetchall())
            by_project = dict(conn.execute("""
                SELECT project_path, COUNT(*) FROM context_saves
                WHERE deleted_at IS NULL
                GROUP BY project_path ORDER BY COUNT(*) DESC
            """).fetchall())
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(created_at) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
        self._json_response(200, {
            "total_records": total,
            "total_sessions": sessions,
            "categories": categories,
            "by_project": by_project,
            "oldest": oldest,
            "newest": newest,
        })

    def _handle_delete_save(self, id_str):
        """DELETE /api/saves/<id>"""
        try:
            save_id = int(id_str)
        except ValueError:
            self._json_response(400, {"ok": False, "error": "invalid id"})
            return
        status, body = self._single_delete_with_fallback(
            where_sql="id = ?",
            params=(save_id,),
        )
        if status == 200 and body.get("ok"):
            body["id"] = save_id
        self._json_response(status, body)

    def _handle_delete_session(self, session_id):
        """DELETE /api/session/<id>"""
        if not session_id or not CTX_DB._validate_session_id(session_id):
            self._json_response(400, {"ok": False, "error": "invalid session_id"})
            return
        status, body = self._single_delete_with_fallback(
            where_sql="session_id = ?",
            params=(session_id,),
        )
        if status == 200 and body.get("ok"):
            body["session_id"] = session_id
        self._json_response(status, body)

    def _single_delete_with_fallback(self, where_sql, params):
        """單筆/單 session 的 hard → soft fallback 刪除（不分批）"""
        try:
            with self.get_db(writer=True) as conn:
                try:
                    cur = conn.execute(
                        f"DELETE FROM context_saves WHERE ({where_sql})",
                        params,
                    )
                    affected = cur.rowcount
                    conn.commit()
                    if affected == 0:
                        return 404, {"ok": False, "error": "record not found"}
                    return 200, {"ok": True, "mode": "hard", "affected": affected}
                except sqlite3.OperationalError as e:
                    logger.warning("hard delete failed, fallback to soft: %s", e)
                    cur = conn.execute(
                        f"UPDATE context_saves SET deleted_at = CURRENT_TIMESTAMP "
                        f"WHERE ({where_sql}) AND deleted_at IS NULL",
                        params,
                    )
                    affected = cur.rowcount
                    conn.commit()
                    if affected == 0:
                        return 404, {"ok": False, "error": "record not found"}
                    return 200, {"ok": True, "mode": "soft", "affected": affected}
        except sqlite3.OperationalError as e:
            logger.error("OperationalError in delete: %s", e)
            return 503, {"ok": False, "error": "db busy or readonly"}

    # --------- Batch delete ---------

    def _handle_batch_delete(self):
        """POST /api/saves/batch-delete（CSRF 已由 do_POST 前置）

        Body: {dry_run: bool, filter: {...}, confirm_token?: str}
        """
        body = self._read_json_body()
        if body is None:
            return

        dry_run = bool(body.get("dry_run", False))
        filter_dict = body.get("filter")
        if filter_dict is None:
            # 向後相容：允許舊格式 {category, before, after, dry_run}
            legacy = {}
            for k in ("category", "before", "after", "project_path"):
                v = body.get(k)
                if v:
                    legacy[k] = v
            filter_dict = legacy
        if not isinstance(filter_dict, dict):
            self._json_response(400, {"ok": False, "error": "filter must be an object"})
            return

        # 白名單 + 值檢查
        allowed_keys = {"category", "before", "after", "project_path"}
        clean_filter = {}
        for k, v in filter_dict.items():
            if k not in allowed_keys:
                continue
            if v is None or v == "":
                continue
            if not isinstance(v, str):
                self._json_response(400, {"ok": False, "error": f"invalid filter.{k}"})
                return
            if len(v) > 512:
                self._json_response(400, {"ok": False, "error": f"filter.{k} too long"})
                return
            # before/after 統一 normalize 為 SQLite CURRENT_TIMESTAMP 格式
            # 防呆：舊版前端/API 呼叫方可能送 ISO 8601（含 T / Z），字典序與
            # 'YYYY-MM-DD HH:MM:SS' 不合會讓 batch delete 範圍誤判（L-16）
            if k in ("before", "after"):
                normalized = v.replace("T", " ")
                if normalized.endswith("Z"):
                    normalized = normalized[:-1]
                if not DATE_FILTER_RE.match(normalized):
                    self._json_response(400, {
                        "ok": False,
                        "error": f"filter.{k} must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS",
                    })
                    return
                clean_filter[k] = normalized
                continue
            clean_filter[k] = v

        if not clean_filter:
            self._json_response(400, {
                "ok": False,
                "error": "at least one of category/before/after/project_path is required",
            })
            return

        where_sql, args = self._build_batch_where(clean_filter)

        if dry_run:
            try:
                with self.get_db() as conn:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM context_saves WHERE {where_sql}",
                        args,
                    ).fetchone()[0]
            except sqlite3.OperationalError as e:
                self._wrap_operational_error(e)
                return
            token = self._make_confirm_token(clean_filter)
            self._json_response(200, {
                "ok": True,
                "dry_run": True,
                "affected": count,
                "filter": clean_filter,
                "confirm_token": token,
            })
            return

        # Confirm：必驗 token
        token = body.get("confirm_token")
        if not self._verify_confirm_token(token, clean_filter):
            logger.warning("batch-delete token mismatch")
            self._json_response(400, {
                "ok": False,
                "error": "filter changed, please preview again",
            })
            return

        # 分批執行（hard → soft fallback）
        try:
            result = self._delete_with_fallback(clean_filter, batch_size=BATCH_SIZE)
        except sqlite3.OperationalError as e:
            self._wrap_operational_error(e)
            return

        self._json_response(200, {
            "ok": True,
            "dry_run": False,
            "mode": result["mode"],
            "affected": result["total_affected"],
            "batches": result["batches"],
            "filter": clean_filter,
        })

    def _build_batch_where(self, filter_dict):
        """依 filter_dict 組 WHERE + args（含 deleted_at IS NULL）"""
        where_parts = ["deleted_at IS NULL"]
        args = []
        if filter_dict.get("category"):
            where_parts.append("category = ?")
            args.append(filter_dict["category"])
        if filter_dict.get("project_path"):
            where_parts.append("project_path = ?")
            args.append(filter_dict["project_path"])
        if filter_dict.get("before"):
            where_parts.append("created_at < ?")
            args.append(filter_dict["before"])
        if filter_dict.get("after"):
            where_parts.append("created_at >= ?")
            args.append(filter_dict["after"])
        return " AND ".join(where_parts), args

    def _delete_with_fallback(self, filter_dict, batch_size=BATCH_SIZE):
        """5000 筆分段刪除；遇 FK/鎖退回軟刪。

        Returns:
            {"total_affected": int, "batches": int, "mode": "hard" | "soft"}
        """
        where_sql, args = self._build_batch_where(filter_dict)
        total_affected = 0
        batches = 0
        mode = "hard"

        with self.get_db(writer=True) as conn:
            # writer=True 已執行 BEGIN IMMEDIATE；isolation_level=None 下，
            # 每輪 commit() 後需顯式 BEGIN IMMEDIATE 才能讓下一輪 DELETE 仍在交易內。
            try:
                while True:
                    cur = conn.execute(
                        f"DELETE FROM context_saves "
                        f"WHERE id IN (SELECT id FROM context_saves "
                        f"             WHERE {where_sql} LIMIT ?)",
                        (*args, batch_size),
                    )
                    n = cur.rowcount or 0
                    conn.commit()
                    if n <= 0:
                        break
                    total_affected += n
                    batches += 1
                    if n < batch_size:
                        break
                    conn.execute("BEGIN IMMEDIATE")
                return {
                    "total_affected": total_affected,
                    "batches": batches,
                    "mode": mode,
                }
            except sqlite3.OperationalError as e:
                logger.warning("hard batch delete failed, fallback to soft: %s", e)
                # 若例外發生於交易中則 rollback；已 commit 的資料無法復原
                # （但 hard→soft fallback 語意本就是「後續改軟刪」，不保證原子性）
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                mode = "soft"
                total_affected = 0
                batches = 0
                conn.execute("BEGIN IMMEDIATE")
                while True:
                    cur = conn.execute(
                        f"UPDATE context_saves SET deleted_at = CURRENT_TIMESTAMP "
                        f"WHERE id IN (SELECT id FROM context_saves "
                        f"             WHERE {where_sql} LIMIT ?)",
                        (*args, batch_size),
                    )
                    n = cur.rowcount or 0
                    conn.commit()
                    if n <= 0:
                        break
                    total_affected += n
                    batches += 1
                    if n < batch_size:
                        break
                    conn.execute("BEGIN IMMEDIATE")
                return {
                    "total_affected": total_affected,
                    "batches": batches,
                    "mode": mode,
                }


# --------------------------------------------------------------------------
# 路徑工具
# --------------------------------------------------------------------------

def _home_relative(path):
    """美化顯示用：將 $HOME 替換為 ~"""
    if path is None:
        return ""
    s = str(path)
    home = str(Path.home())
    if s.startswith(home):
        return "~" + s[len(home):]
    return s


def _resolve_db_path(arg_db):
    """決定 DB 路徑：--db > CTX_SAVE_DB_PATH > 預設"""
    if arg_db:
        return Path(arg_db).expanduser().resolve()
    env = os.environ.get("CTX_SAVE_DB_PATH")
    if env:
        return Path(env).expanduser().resolve()
    default_dir = Path.home() / ".ctx-save"
    return (default_dir / "context.db").resolve()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Context Save 視覺化 Web Server")
    parser.add_argument("--db", help="SQLite 資料庫路徑（預設 ~/.ctx-save/context.db）")
    parser.add_argument("--port", type=int, default=29898, help="HTTP port（預設 29898）")
    parser.add_argument("--open", action="store_true", help="啟動後自動開啟瀏覽器")
    args = parser.parse_args()

    global DB_PATH
    DB_PATH = _resolve_db_path(args.db)

    # 確保父目錄存在
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"  !! 無法建立 DB 目錄 {DB_PATH.parent}: {e}")
        sys.exit(1)

    # DB 不存在時呼叫 ctx-db.init_db 建立空 DB；隨後跑 migration（冪等）
    try:
        if not DB_PATH.exists():
            CTX_DB.init_db(DB_PATH)
            print(f"  i 已建立空 DB: {_home_relative(DB_PATH)}")
        # 跑 migration chain（冪等）
        with CTX_DB._connect_db(DB_PATH, writer=True) as conn:
            CTX_DB._run_all_migrations(conn)
    except Exception as e:
        logger.error("migration failed: %s\n%s", e, traceback.format_exc())
        print(f"  !! Migration 失敗（繼續啟動，但部分功能可能異常）: {e}")

    url = f"http://localhost:{args.port}"
    print("")
    print("  Context Save Viewer")
    print("  ==============================")
    print(f"  DB     : {_home_relative(DB_PATH)}")
    print(f"  URL    : {url}")
    print(f"  Version: {VERSION}")
    print(f"  Started: {STARTED_AT}")
    print("  ==============================")
    print("  Ctrl+C 停止")
    print("")

    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), RequestHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()


if __name__ == "__main__":
    main()
