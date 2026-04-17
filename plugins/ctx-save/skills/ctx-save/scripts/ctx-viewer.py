#!/usr/bin/env python3
"""ctx-viewer.py — Context Save 視覺化 Web Server

啟動方式:
    python3 ctx-viewer.py                          # 自動搜尋當前目錄的 .ctx-save/context.db
    python3 ctx-viewer.py --db /path/to/context.db  # 指定 DB 路徑
    python3 ctx-viewer.py --port 8787               # 指定 port（預設 8787）
    python3 ctx-viewer.py --open                    # 啟動後自動開啟瀏覽器

無需任何外部依賴，僅使用 Python 標準庫。
"""

import argparse
import json
import os
import sqlite3
import sys
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DB_PATH = None

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
  .header-stats { display: flex; gap: 16px; margin-left: auto; font-size: 13px; color: var(--text2); }
  .header-stats strong { color: var(--text); }

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

async function fetchAPI(endpoint) {
  const res = await fetch('/api/' + endpoint);
  return res.json();
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
  div.textContent = text;
  return div.innerHTML;
}

async function loadSessions() {
  const data = await fetchAPI('sessions');
  allSessions = data.sessions || [];
  const categories = new Set();
  allSessions.forEach(s => {
    (s.categories || '').split(',').forEach(c => { if (c.trim()) categories.add(c.trim()); });
  });
  const select = document.getElementById('categoryFilter');
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
      <div class="session-item" data-id="${s.session_id}" onclick="selectSession('${s.session_id}')">
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
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === sessionId);
  });

  let records = allRecords[sessionId];
  if (!records) {
    const data = await fetchAPI('session/' + encodeURIComponent(sessionId));
    records = data.records || [];
    allRecords[sessionId] = records;
  }

  const content = document.getElementById('contentPanel');
  if (!records.length) {
    content.innerHTML = '<div class="empty-state"><div class="icon">&#128196;</div><div>此 session 無記錄</div></div>';
    return;
  }

  const first = records[0];
  const header = `
    <div class="detail-header">
      <h2>${escapeHtml(first.title || sessionId)}</h2>
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
    return `
      <div class="record-card">
        <div class="record-header">
          <span class="emoji">${cat.emoji}</span>
          <span>${cat.label}</span>
          <span style="margin-left: auto; font-weight: 400; font-size: 12px; color: var(--text2);">${escapeHtml(r.title)}</span>
        </div>
        <div class="record-body">${escapeHtml(r.content)}</div>
        ${tagsSection}
      </div>
    `;
  }).join('');

  content.innerHTML = header + cards;
}

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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self.send_html(HTML_PAGE)

        elif path == "/api/sessions":
            conn = self.get_db()
            rows = conn.execute("""
                SELECT
                    session_id,
                    MIN(created_at) as created_at,
                    GROUP_CONCAT(DISTINCT category) as categories,
                    MAX(context_percentage) as context_percentage,
                    COUNT(*) as item_count,
                    GROUP_CONCAT(title, ' | ') as titles
                FROM context_saves
                GROUP BY session_id
                ORDER BY created_at DESC
            """).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM context_saves").fetchone()[0]
            conn.close()
            sessions = [dict(r) for r in rows]
            self.send_json({"sessions": sessions, "total_records": total})

        elif path.startswith("/api/session/"):
            session_id = path.split("/api/session/", 1)[1]
            conn = self.get_db()
            rows = conn.execute("""
                SELECT category, title, content, tags, context_percentage, created_at, model
                FROM context_saves
                WHERE session_id = ?
                ORDER BY id
            """, (session_id,)).fetchall()
            conn.close()
            self.send_json({"records": [dict(r) for r in rows]})

        elif path == "/api/stats":
            conn = self.get_db()
            total = conn.execute("SELECT COUNT(*) FROM context_saves").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM context_saves").fetchone()[0]
            categories = dict(conn.execute(
                "SELECT category, COUNT(*) FROM context_saves GROUP BY category ORDER BY COUNT(*) DESC"
            ).fetchall())
            oldest = conn.execute("SELECT MIN(created_at) FROM context_saves").fetchone()[0]
            newest = conn.execute("SELECT MAX(created_at) FROM context_saves").fetchone()[0]
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
                SELECT session_id, created_at, category, title, content
                FROM context_saves
                WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?
                ORDER BY created_at DESC LIMIT 50
            """, (like, like, like)).fetchall()
            conn.close()
            self.send_json({"results": [dict(r) for r in rows]})

        else:
            self.send_response(404)
            self.end_headers()


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


def main():
    parser = argparse.ArgumentParser(description="Context Save 視覺化 Web Server")
    parser.add_argument("--db", help="SQLite 資料庫路徑")
    parser.add_argument("--port", type=int, default=8787, help="HTTP port（預設 8787）")
    parser.add_argument("--open", action="store_true", help="啟動後自動開啟瀏覽器")
    args = parser.parse_args()

    global DB_PATH

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

    url = f"http://localhost:{args.port}"
    print(f"")
    print(f"  ⚙ Context Save Viewer")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  📂 DB:   {DB_PATH}")
    print(f"  🌐 URL:  {url}")
    print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Ctrl+C 停止")
    print(f"")

    if args.open:
        webbrowser.open(url)

    server = HTTPServer(("0.0.0.0", args.port), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
        server.server_close()


if __name__ == "__main__":
    main()
