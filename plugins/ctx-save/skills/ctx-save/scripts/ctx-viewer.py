#!/usr/bin/env python3
"""ctx-viewer.py — Context Save 視覺化 Web Server

啟動方式:
    python3 ctx-viewer.py                          # 自動搜尋當前目錄的 .ctx-save/context.db
    python3 ctx-viewer.py --db /path/to/context.db  # 指定 DB 路徑
    python3 ctx-viewer.py --port 29898               # 指定 port（預設 29898）
    python3 ctx-viewer.py --open                    # 啟動後自動開啟瀏覽器

無需任何外部依賴，僅使用 Python 標準庫。
"""

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import webbrowser
from datetime import datetime
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 全域設定（供 /api/ping 與各 Handler 共用）
DB_PATH = None
SERVICE_NAME = "ctx-viewer"
VERSION = "2.0.0"
STARTED_AT = None  # main() 會初始化為 ISO8601 字串

HTML_PAGE = """<!DOCTYPE html>
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
  .header h1 { font-size: 18px; font-weight: 600; }
  .header h1 span { color: var(--accent); }
  .search-box {
    flex: 1; max-width: 400px;
    background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 8px 12px; color: var(--text); font-size: 14px; outline: none;
  }
  .search-box:focus { border-color: var(--accent); }
  .search-box::placeholder { color: var(--text2); }
  .header-stats { display: flex; gap: 16px; font-size: 13px; color: var(--text2); }
  .header-stats strong { color: var(--text); }

  /* Header 批次刪除按鈕 */
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
    font-size: 13px; color: var(--text2); display: flex; align-items: center; gap: 8px;
  }
  .sidebar-header select {
    background: var(--bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 4px; padding: 4px 8px; font-size: 12px; outline: none;
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

  /* 記錄操作按鈕列 */
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

  /* 整個 session 刪除按鈕 */
  .btn-delete-session {
    background: transparent; border: 1px solid var(--border);
    color: var(--danger); padding: 6px 12px; border-radius: var(--radius);
    font-size: 13px; cursor: pointer; transition: background 0.15s;
  }
  .btn-delete-session:hover { background: rgba(248, 81, 73, 0.1); }

  /* Stats view */
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
  .chart-bar-label { width: 80px; font-size: 13px; color: var(--text2); text-align: right; }
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

  /* Scrollbar */
  ::-webkit-scrollbar { width: 8px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text2); }

  /* Modal（批次刪除面板） */
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
  }
</style>
</head>
<body>

<div class="header">
  <h1><span>&#9881;</span> Context Save Viewer</h1>
  <input class="search-box" type="text" placeholder="搜尋關鍵字..." id="searchInput">
  <div class="header-stats" id="headerStats"></div>
  <button class="btn-batch-delete" onclick="openBatchDeleteModal()">&#128465; 批次刪除</button>
</div>

<div class="tabs">
  <div class="tab active" data-tab="sessions" onclick="switchTab('sessions')">Sessions</div>
  <div class="tab" data-tab="stats" onclick="switchTab('stats')">Stats</div>
</div>

<div class="main">
  <div class="sidebar" id="sidebarPanel">
    <div class="sidebar-header">
      <span id="sessionCount">0 sessions</span>
      <select id="categoryFilter" onchange="filterSessions()">
        <option value="">全部類別</option>
      </select>
      <select id="sortOrder" onchange="filterSessions()">
        <option value="newest">最新優先</option>
        <option value="oldest">最舊優先</option>
        <option value="ctx-high">Context% 高→低</option>
      </select>
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
<div class="modal-overlay" id="batchDeleteModal" onclick="onModalOverlayClick(event)">
  <div class="modal-content" onclick="event.stopPropagation()">
    <div class="modal-title">
      <span>&#128465;</span>
      <span>批次刪除記錄</span>
      <button class="modal-close" onclick="closeBatchDeleteModal()">&times;</button>
    </div>
    <div class="modal-hint">至少指定一個條件（分類 / 早於 / 晚於）才可執行刪除。</div>
    <div class="form-group">
      <label>分類</label>
      <select id="batchCategory">
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
      <button class="btn-secondary" onclick="closeBatchDeleteModal()">取消</button>
      <button class="btn-primary" id="btnBatchPreview" onclick="previewBatchDelete()">預覽筆數</button>
      <button class="btn-danger" id="btnBatchConfirm" onclick="confirmBatchDelete()" disabled>確認刪除</button>
    </div>
  </div>
</div>

<!-- Toast container -->
<div class="toast-container" id="toastContainer"></div>

<script>
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
let batchPreviewCount = null; // 預覽過才開放確認刪除

async function fetchAPI(endpoint) {
  const res = await fetch('/api/' + endpoint);
  return res.json();
}

async function fetchJSON(url, opts = {}) {
  const res = await fetch(url, opts);
  const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
  return { status: res.status, data };
}

function formatDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const pad = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function ctxBadgeClass(pct) {
  if (pct >= 70) return 'ctx-high';
  if (pct >= 50) return 'ctx-mid';
  return 'ctx-low';
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text == null ? '' : String(text);
  return div.innerHTML;
}

/* ========== Toast 機制 ========== */
function showToast(msg, type = 'success') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast' + (type === 'error' ? ' error' : type === 'warn' ? ' warn' : '');
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => toast.remove(), 300);
  }, 2000);
}

/* ========== Sessions 載入與渲染 ========== */
async function loadSessions() {
  const data = await fetchAPI('sessions');
  allSessions = data.sessions || [];
  allRecords = {}; // 清掉快取，確保刪除後不會顯示舊資料
  const categories = new Set();
  allSessions.forEach(s => {
    (s.categories || '').split(',').forEach(c => { if (c.trim()) categories.add(c.trim()); });
  });
  const select = document.getElementById('categoryFilter');
  // 清空後重建（除了第一個「全部類別」選項）
  while (select.options.length > 1) select.remove(1);
  categories.forEach(c => {
    const cat = CATEGORY_MAP[c] || { emoji: '📌', label: c };
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = `${cat.emoji} ${cat.label}`;
    select.appendChild(opt);
  });
  document.getElementById('headerStats').innerHTML =
    `<span>Sessions: <strong>${allSessions.length}</strong></span>` +
    `<span>Records: <strong>${data.total_records || 0}</strong></span>`;
  filterSessions();
}

function filterSessions() {
  const keyword = document.getElementById('searchInput').value.toLowerCase();
  const category = document.getElementById('categoryFilter').value;
  const sort = document.getElementById('sortOrder').value;

  let filtered = allSessions.filter(s => {
    if (category && !(s.categories || '').includes(category)) return false;
    if (keyword && !(s.titles || '').toLowerCase().includes(keyword)
        && !(s.session_id || '').toLowerCase().includes(keyword)) return false;
    return true;
  });

  if (sort === 'oldest') filtered.sort((a, b) => a.created_at.localeCompare(b.created_at));
  else if (sort === 'ctx-high') filtered.sort((a, b) => (b.context_percentage || 0) - (a.context_percentage || 0));
  else filtered.sort((a, b) => b.created_at.localeCompare(a.created_at));

  document.getElementById('sessionCount').textContent = `${filtered.length} sessions`;
  const list = document.getElementById('sessionList');
  list.innerHTML = filtered.map(s => {
    const cats = (s.categories || '').split(',').filter(Boolean);
    const catBadges = cats.map(c => {
      const cat = CATEGORY_MAP[c.trim()] || { emoji: '📌', label: c };
      return `<span class="category-badge">${cat.emoji} ${cat.label}</span>`;
    }).join('');
    const ctxPct = s.context_percentage || 0;
    const title = (s.titles || '').split(' | ')[0] || s.session_id.slice(0, 12);
    return `
      <div class="session-item" data-id="${escapeHtml(s.session_id)}" onclick="selectSession('${escapeHtml(s.session_id)}')">
        <div class="session-date">${formatDate(s.created_at)}</div>
        <div class="session-title">${escapeHtml(title)}</div>
        <div class="session-meta">
          ${catBadges}
          <span class="ctx-badge ${ctxBadgeClass(ctxPct)}">${ctxPct}%</span>
          <span class="category-badge">${s.item_count} 項</span>
        </div>
      </div>
    `;
  }).join('');
}

async function selectSession(sessionId) {
  currentSessionId = sessionId;
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === sessionId);
  });

  let records = allRecords[sessionId];
  if (!records) {
    const data = await fetchAPI('session/' + encodeURIComponent(sessionId));
    records = data.records || [];
    allRecords[sessionId] = records;
  }

  renderSessionDetail(sessionId, records);
}

function renderSessionDetail(sessionId, records) {
  const content = document.getElementById('contentPanel');
  if (!records.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">&#128196;</div><div>此 session 無記錄</div></div>';
    return;
  }

  const first = records[0];
  const header = `
    <div class="detail-header">
      <div class="detail-header-top">
        <h2>${escapeHtml(first.title || sessionId)}</h2>
        <button class="btn-delete-session" onclick="deleteSession('${escapeHtml(sessionId)}')">&#128465; 刪除整個 session</button>
      </div>
      <div class="detail-meta">
        <span>&#128197; ${formatDate(first.created_at)}</span>
        <span>&#129302; ${escapeHtml(first.model || 'N/A')}</span>
        <span>&#128202; Context: ${first.context_percentage || 0}%</span>
        <span>&#128203; ${records.length} 項記錄</span>
      </div>
    </div>
  `;

  const cards = records.map(r => {
    const cat = CATEGORY_MAP[r.category] || { emoji: '📌', label: r.category };
    const tags = (r.tags || '').split(',').filter(Boolean).map(t =>
      `<span class="tag">${escapeHtml(t.trim())}</span>`
    ).join('');
    const tagsSection = tags ? `<div class="record-tags">${tags}</div>` : '';
    // 用 data-id 標記列，刪除後可從 DOM 移除
    return `
      <div class="record-card" data-record-id="${r.id}">
        <div class="record-header">
          <span class="emoji">${cat.emoji}</span>
          <span>${cat.label}</span>
          <span style="margin-left: auto; font-weight: 400; font-size: 12px; color: var(--text2);">${escapeHtml(r.title)}</span>
        </div>
        <div class="record-body">${escapeHtml(r.content)}</div>
        ${tagsSection}
        <div class="record-actions">
          <button class="btn-action btn-delete" onclick="deleteRecord(${r.id})">&#128465; 刪除</button>
          <button class="btn-action" onclick="copyContent(${r.id})">&#128203; 複製內容</button>
          <button class="btn-action" onclick="copyMarkdown(${r.id})">&#128203; 複製 MD</button>
        </div>
      </div>
    `;
  }).join('');

  content.innerHTML = header + cards;
}

/* ========== 刪除操作 ========== */
async function deleteRecord(id) {
  if (!confirm('確定刪除此筆記錄？')) return;
  const { status, data } = await fetchJSON(`/api/saves/${id}`, { method: 'DELETE' });
  if (status === 200 && data.ok) {
    // 從 DOM 移除該列
    const card = document.querySelector(`.record-card[data-record-id="${id}"]`);
    if (card) card.remove();
    // 更新快取
    if (currentSessionId && allRecords[currentSessionId]) {
      allRecords[currentSessionId] = allRecords[currentSessionId].filter(r => r.id !== id);
    }
    const modeMsg = data.mode === 'soft' ? '已軟刪除（DB 忙碌中）' : '已刪除';
    showToast(`✅ ${modeMsg}`, data.mode === 'soft' ? 'warn' : 'success');
  } else if (status === 404) {
    showToast('❌ 記錄不存在', 'error');
  } else {
    showToast('❌ 刪除失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

async function deleteSession(sessionId) {
  if (!confirm(`確定刪除整個 session 的所有記錄？\n\nSession ID: ${sessionId}`)) return;
  const { status, data } = await fetchJSON(`/api/session/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
  if (status === 200 && data.ok) {
    const modeMsg = data.mode === 'soft' ? '已軟刪除' : '已刪除';
    showToast(`✅ ${modeMsg} ${data.affected} 筆記錄`, data.mode === 'soft' ? 'warn' : 'success');
    // 重載 sessions 列表
    await loadSessions();
    // 清空內容區
    currentSessionId = null;
    document.getElementById('contentPanel').innerHTML = `
      <div class="empty-state">
        <div class="icon">&#128203;</div>
        <div>選擇左側的 session 查看詳情</div>
      </div>
    `;
  } else if (status === 404) {
    showToast('❌ Session 不存在', 'error');
  } else {
    showToast('❌ 刪除失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

/* ========== 複製操作 ========== */
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
  } catch (e) {
    // Fallback：用 prompt 顯示讓使用者手動複製
    try {
      prompt('請手動複製（navigator.clipboard 無法使用）:', text);
      return true;
    } catch (e2) {
      return false;
    }
  }
}

async function copyContent(id) {
  const r = findRecordById(id);
  if (!r) { showToast('❌ 找不到記錄', 'error'); return; }
  const ok = await copyToClipboard(r.content || '');
  if (ok) showToast('✅ 已複製內容');
  else showToast('❌ 複製失敗', 'error');
}

async function copyMarkdown(id) {
  const r = findRecordById(id);
  if (!r) { showToast('❌ 找不到記錄', 'error'); return; }
  const md = [
    '---',
    `title: ${r.title || ''}`,
    `category: ${r.category || ''}`,
    `tags: ${r.tags || ''}`,
    `context_percentage: ${r.context_percentage || 0}%`,
    `session_id: ${r.session_id || currentSessionId || ''}`,
    `created_at: ${r.created_at || ''}`,
    `model: ${r.model || ''}`,
    '---',
    '',
    r.content || '',
  ].join('\n');
  const ok = await copyToClipboard(md);
  if (ok) showToast('✅ 已複製 Markdown');
  else showToast('❌ 複製失敗', 'error');
}

/* ========== 批次刪除 Modal ========== */
async function openBatchDeleteModal() {
  // 從 /api/stats 取得 categories，填入下拉
  const stats = await fetchAPI('stats');
  const select = document.getElementById('batchCategory');
  while (select.options.length > 1) select.remove(1);
  const cats = Object.keys(stats.categories || {});
  cats.forEach(c => {
    const cat = CATEGORY_MAP[c] || { emoji: '📌', label: c };
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = `${cat.emoji} ${cat.label} (${stats.categories[c]})`;
    select.appendChild(opt);
  });
  // 重置
  document.getElementById('batchBefore').value = '';
  document.getElementById('batchAfter').value = '';
  document.getElementById('batchPreview').style.display = 'none';
  document.getElementById('btnBatchConfirm').disabled = true;
  batchPreviewCount = null;

  document.getElementById('batchDeleteModal').classList.add('show');
}

function closeBatchDeleteModal() {
  document.getElementById('batchDeleteModal').classList.remove('show');
}

function onModalOverlayClick(e) {
  if (e.target.id === 'batchDeleteModal') closeBatchDeleteModal();
}

function collectBatchFilter() {
  const category = document.getElementById('batchCategory').value || null;
  const beforeDate = document.getElementById('batchBefore').value;
  const afterDate = document.getElementById('batchAfter').value;
  const before = beforeDate ? `${beforeDate}T00:00:00Z` : null;
  const after = afterDate ? `${afterDate}T00:00:00Z` : null;
  return { category, before, after };
}

async function previewBatchDelete() {
  const filter = collectBatchFilter();
  if (!filter.category && !filter.before && !filter.after) {
    showToast('❌ 請至少指定一個條件', 'error');
    return;
  }
  const { status, data } = await fetchJSON('/api/saves/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...filter, dry_run: true }),
  });
  const preview = document.getElementById('batchPreview');
  if (status === 200 && data.ok) {
    batchPreviewCount = data.affected || 0;
    preview.textContent = `將刪除 ${batchPreviewCount} 筆記錄`;
    preview.style.display = 'block';
    document.getElementById('btnBatchConfirm').disabled = batchPreviewCount === 0;
  } else {
    preview.style.display = 'none';
    batchPreviewCount = null;
    document.getElementById('btnBatchConfirm').disabled = true;
    showToast('❌ 預覽失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

async function confirmBatchDelete() {
  if (batchPreviewCount == null) {
    showToast('❌ 請先預覽筆數', 'error');
    return;
  }
  if (!confirm(`確定刪除 ${batchPreviewCount} 筆記錄？此動作無法復原。`)) return;
  const filter = collectBatchFilter();
  const { status, data } = await fetchJSON('/api/saves/batch-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...filter, dry_run: false }),
  });
  if (status === 200 && data.ok) {
    const modeMsg = data.mode === 'soft' ? '已軟刪除' : '已刪除';
    showToast(`✅ ${modeMsg} ${data.affected} 筆記錄`, data.mode === 'soft' ? 'warn' : 'success');
    closeBatchDeleteModal();
    // 重整 sessions 列表
    await loadSessions();
    currentSessionId = null;
    document.getElementById('contentPanel').innerHTML = `
      <div class="empty-state">
        <div class="icon">&#128203;</div>
        <div>選擇左側的 session 查看詳情</div>
      </div>
    `;
  } else {
    showToast('❌ 刪除失敗：' + (data.error || '未知錯誤'), 'error');
  }
}

/* ========== Stats Tab ========== */
async function renderStats() {
  const data = await fetchAPI('stats');
  const content = document.getElementById('contentPanel');

  const catEntries = Object.entries(data.categories || {});
  const maxCount = Math.max(...catEntries.map(e => e[1]), 1);
  const bars = catEntries.sort((a, b) => b[1] - a[1]).map(([cat, count]) => {
    const c = CATEGORY_MAP[cat] || { emoji: '📌', label: cat };
    const pct = (count / maxCount * 100).toFixed(0);
    return `
      <div class="chart-bar-row">
        <div class="chart-bar-label">${c.emoji} ${c.label}</div>
        <div class="chart-bar-track">
          <div class="chart-bar-fill" style="width: ${pct}%">${count}</div>
        </div>
      </div>
    `;
  }).join('');

  content.innerHTML = `
    <div class="stats-grid">
      <div class="stat-card">
        <div class="label">總記錄數</div>
        <div class="value accent">${data.total_records || 0}</div>
      </div>
      <div class="stat-card">
        <div class="label">總 Session 數</div>
        <div class="value green">${data.total_sessions || 0}</div>
      </div>
      <div class="stat-card">
        <div class="label">類別數</div>
        <div class="value purple">${catEntries.length}</div>
      </div>
      <div class="stat-card">
        <div class="label">時間範圍</div>
        <div class="value warn" style="font-size: 16px;">
          ${data.oldest ? formatDate(data.oldest).split(' ')[0] : 'N/A'}<br>
          ~ ${data.newest ? formatDate(data.newest).split(' ')[0] : 'N/A'}
        </div>
      </div>
    </div>
    <h3 style="margin-bottom: 16px;">各類別分布</h3>
    <div class="chart-bar-container">${bars || '<div style="color:var(--text2)">尚無資料</div>'}</div>
  `;
}

function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  const sidebar = document.getElementById('sidebarPanel');
  if (tab === 'stats') {
    sidebar.style.display = 'none';
    renderStats();
  } else {
    sidebar.style.display = '';
    document.getElementById('contentPanel').innerHTML = `
      <div class="empty-state">
        <div class="icon">&#128203;</div>
        <div>選擇左側的 session 查看詳情</div>
      </div>
    `;
  }
}

document.getElementById('searchInput').addEventListener('input', () => {
  clearTimeout(window._searchTimer);
  window._searchTimer = setTimeout(filterSessions, 200);
});

loadSessions();
</script>
</body>
</html>"""


class RequestHandler(BaseHTTPRequestHandler):
    """處理 API 請求與靜態頁面"""

    def log_message(self, format, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [{ts}] {args[0]}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def get_db(self):
        """取得 SQLite 連線（row_factory + 1 秒 busy_timeout）"""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 1000")
        return conn

    # ======================= GET =======================
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_html(HTML_PAGE)

        elif path == "/api/ping":
            # 健康檢查端點，供 launcher 判斷既有服務是否為本 plugin
            self.send_json({
                "ok": True,
                "service": SERVICE_NAME,
                "version": VERSION,
                "db": DB_PATH,
                "started_at": STARTED_AT,
            })

        elif path == "/api/sessions":
            conn = self.get_db()
            # 所有 SELECT 都過濾 deleted_at IS NULL
            rows = conn.execute("""
                SELECT
                    session_id,
                    MIN(created_at) as created_at,
                    GROUP_CONCAT(DISTINCT category) as categories,
                    MAX(context_percentage) as context_percentage,
                    COUNT(*) as item_count,
                    GROUP_CONCAT(title, ' | ') as titles
                FROM context_saves
                WHERE deleted_at IS NULL
                GROUP BY session_id
                ORDER BY created_at DESC
            """).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
            conn.close()
            sessions = [dict(r) for r in rows]
            self.send_json({"sessions": sessions, "total_records": total})

        elif path.startswith("/api/session/"):
            session_id = path.split("/api/session/", 1)[1]
            conn = self.get_db()
            rows = conn.execute("""
                SELECT id, session_id, category, title, content, tags,
                       context_percentage, created_at, model
                FROM context_saves
                WHERE session_id = ? AND deleted_at IS NULL
                ORDER BY id
            """, (session_id,)).fetchall()
            conn.close()
            self.send_json({"records": [dict(r) for r in rows]})

        elif path == "/api/stats":
            conn = self.get_db()
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
            oldest = conn.execute(
                "SELECT MIN(created_at) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(created_at) FROM context_saves WHERE deleted_at IS NULL"
            ).fetchone()[0]
            conn.close()
            self.send_json({
                "total_records": total,
                "total_sessions": sessions,
                "categories": categories,
                "oldest": oldest,
                "newest": newest,
            })

        elif path == "/api/search":
            keyword = params.get("q", [""])[0]
            if not keyword:
                self.send_json({"results": []})
                return
            conn = self.get_db()
            like = f"%{keyword}%"
            rows = conn.execute("""
                SELECT id, session_id, created_at, category, title, content
                FROM context_saves
                WHERE (title LIKE ? OR content LIKE ? OR tags LIKE ?)
                  AND deleted_at IS NULL
                ORDER BY created_at DESC LIMIT 50
            """, (like, like, like)).fetchall()
            conn.close()
            self.send_json({"results": [dict(r) for r in rows]})

        else:
            self.send_response(404)
            self.end_headers()

    # ======================= DELETE =======================
    def do_DELETE(self):
        """
        路由：
          - DELETE /api/saves/{id}            → 刪除單筆
          - DELETE /api/session/{session_id}  → 刪除整個 session
        """
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/saves/"):
            id_str = path.split("/api/saves/", 1)[1]
            try:
                save_id = int(id_str)
            except ValueError:
                self.send_json({"ok": False, "error": "invalid id"}, status=400)
                return
            status, body = self._delete_with_fallback(
                where_sql="id = ?",
                params=(save_id,),
            )
            if status == 200 and body.get("ok"):
                body["id"] = save_id
            self.send_json(body, status=status)
            return

        if path.startswith("/api/session/"):
            session_id = path.split("/api/session/", 1)[1]
            if not session_id:
                self.send_json({"ok": False, "error": "session_id required"}, status=400)
                return
            status, body = self._delete_with_fallback(
                where_sql="session_id = ?",
                params=(session_id,),
                is_session=True,
            )
            if status == 200 and body.get("ok"):
                body["session_id"] = session_id
            self.send_json(body, status=status)
            return

        self.send_response(404)
        self.end_headers()

    # ======================= POST =======================
    def do_POST(self):
        """路由：POST /api/saves/batch-delete"""
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/saves/batch-delete":
            self._handle_batch_delete()
            return

        self.send_response(404)
        self.end_headers()

    # ======================= 內部輔助方法 =======================
    def _delete_with_fallback(self, where_sql, params, is_session=False):
        """
        共用的 hard → soft 降級刪除邏輯。

        Args:
            where_sql: WHERE 子句（不含 WHERE 關鍵字）
            params: 對應的參數 tuple
            is_session: True 表示整個 session 刪除（rowcount 不代表存在）

        Returns:
            (status_code, response_body_dict)
        """
        conn = self.get_db()
        try:
            try:
                cur = conn.execute(
                    f"DELETE FROM context_saves WHERE {where_sql}",
                    params,
                )
                affected = cur.rowcount
                conn.commit()
                if affected == 0:
                    return 404, {"ok": False, "error": "record not found"}
                return 200, {"ok": True, "mode": "hard", "affected": affected}
            except sqlite3.OperationalError as e:
                # DB 唯讀 / locked → 軟刪除 fallback
                cur = conn.execute(
                    f"UPDATE context_saves SET deleted_at = CURRENT_TIMESTAMP "
                    f"WHERE ({where_sql}) AND deleted_at IS NULL",
                    params,
                )
                affected = cur.rowcount
                conn.commit()
                if affected == 0:
                    return 404, {"ok": False, "error": "record not found"}
                return 200, {
                    "ok": True,
                    "mode": "soft",
                    "affected": affected,
                    "reason": str(e),
                }
        finally:
            conn.close()

    def _handle_batch_delete(self):
        """處理 POST /api/saves/batch-delete"""
        # 1. 讀 body
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, json.JSONDecodeError) as e:
            self.send_json({"ok": False, "error": f"invalid JSON body: {e}"}, status=400)
            return

        category = body.get("category")
        before = body.get("before")
        after = body.get("after")
        dry_run = bool(body.get("dry_run", False))

        # 2. 驗證至少一個條件
        if not category and not before and not after:
            self.send_json(
                {"ok": False, "error": "at least one of category/before/after is required"},
                status=400,
            )
            return

        # 3. 動態組 WHERE
        where_parts = ["deleted_at IS NULL"]
        params = []
        if category:
            where_parts.append("category = ?")
            params.append(category)
        if before:
            where_parts.append("created_at < ?")
            params.append(before)
        if after:
            where_parts.append("created_at >= ?")
            params.append(after)
        where_sql = " AND ".join(where_parts)
        filter_dict = {}
        if category: filter_dict["category"] = category
        if before: filter_dict["before"] = before
        if after: filter_dict["after"] = after

        conn = self.get_db()
        try:
            # 4. Dry run：只 SELECT COUNT
            if dry_run:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM context_saves WHERE {where_sql}",
                    params,
                ).fetchone()[0]
                self.send_json({
                    "ok": True,
                    "dry_run": True,
                    "affected": count,
                    "filter": filter_dict,
                })
                return

            # 5. 實際執行：hard → soft fallback
            try:
                cur = conn.execute(
                    f"DELETE FROM context_saves WHERE {where_sql}",
                    params,
                )
                affected = cur.rowcount
                conn.commit()
                self.send_json({
                    "ok": True,
                    "dry_run": False,
                    "mode": "hard",
                    "affected": affected,
                    "filter": filter_dict,
                })
            except sqlite3.OperationalError as e:
                cur = conn.execute(
                    f"UPDATE context_saves SET deleted_at = CURRENT_TIMESTAMP "
                    f"WHERE {where_sql}",
                    params,
                )
                affected = cur.rowcount
                conn.commit()
                self.send_json({
                    "ok": True,
                    "dry_run": False,
                    "mode": "soft",
                    "affected": affected,
                    "reason": str(e),
                    "filter": filter_dict,
                })
        finally:
            conn.close()


def find_db():
    """從當前目錄往上搜尋 .ctx-save/context.db"""
    cwd = os.getcwd()
    while True:
        candidate = os.path.join(cwd, ".ctx-save", "context.db")
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    return None


def _run_migration():
    """啟動前執行 schema migration（冪等）。

    因 ctx-db.py 檔名含連字號無法用 import 語法，改用 importlib 動態載入。
    """
    try:
        spec_ = importlib.util.spec_from_file_location(
            "ctx_db", Path(__file__).parent / "ctx-db.py"
        )
        ctx_db = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(ctx_db)
    except Exception as e:
        print(f"  ⚠ 無法載入 ctx-db.py（跳過 migration）: {e}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        if ctx_db.migrate_add_deleted_at(conn):
            print("  ✓ 資料庫已升級（新增 deleted_at 欄位）")
        conn.close()
    except Exception as e:
        print(f"  ⚠ Migration 執行失敗（繼續啟動）: {e}")


def main():
    parser = argparse.ArgumentParser(description="Context Save 視覺化 Web Server")
    parser.add_argument("--db", help="SQLite 資料庫路徑")
    parser.add_argument("--port", type=int, default=29898, help="HTTP port（預設 29898）")
    parser.add_argument("--open", action="store_true", help="啟動後自動開啟瀏覽器")
    args = parser.parse_args()

    global DB_PATH, STARTED_AT

    if args.db:
        DB_PATH = args.db
    else:
        DB_PATH = find_db()

    if not DB_PATH or not os.path.exists(DB_PATH):
        print("❌ 找不到 context.db")
        print("")
        print("   請先用 /ctx-save 儲存至少一筆資料（選擇 SQLite 模式），")
        print("   或用 --db 參數指定路徑：")
        print("")
        print(f"   python3 {sys.argv[0]} --db /path/to/.ctx-save/context.db")
        sys.exit(1)

    # 啟動前執行 schema migration（冪等）
    _run_migration()

    # 記錄啟動時間（ISO8601 UTC）
    STARTED_AT = datetime.utcnow().isoformat() + "Z"

    # 對外僅 127.0.0.1（只能本機連線，見 arch.md §10 安全邊界）
    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  ⚙ Context Save Viewer")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  📂 DB:      {DB_PATH}")
    print(f"  🌐 URL:     {url}")
    print(f"  🏷️  Version: {VERSION}")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Ctrl+C 停止")
    print(f"")

    if args.open:
        webbrowser.open(url)

    # bind 127.0.0.1 — 僅限本機連線
    # 使用 ThreadingHTTPServer 避免單一慢請求阻塞 /api/ping 等健康檢查
    server = ThreadingHTTPServer(("127.0.0.1", args.port), RequestHandler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()


if __name__ == "__main__":
    main()
