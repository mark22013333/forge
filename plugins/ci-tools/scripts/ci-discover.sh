#!/usr/bin/env bash
# ci-discover.sh — 導入 CI 前先探索既有設定（唯讀、無副作用）
#
# 很多時候 ADO server 早就裝好了，資訊散在其他專案的 azure-pipelines.yml、~/.ssh 的金鑰、
# shell 的環境變數裡。這支腳本把它們找出來，再建議下一步，避免從零開始重裝。
#
# 另外在套 pipeline 範本前先揪出兩類風險（純本機掃描、不連線）：
#   - 測試外部連線：測試設定與測試程式碼裡的連線字串（與 ADO server 同機、agent 本機），
#     以及會啟動 Spring context 的測試類；連線字串只顯示 scheme、主機、埠，不顯示帳密
#   - 跨 repo 相對引用：前端檔 import／require／@import 的相對路徑跳出 git repo 根目錄
#
# 只做讀取：不改任何設定、不寫使用者目錄；探測回應暫存在 $TMPDIR 的 mktemp 目錄（不自動清理）。
# 網路只發 GET（Gitea／GitLab 版本端點、ADO 查詢 API）。
# 權杖只從環境變數讀，不會印出。
#
# 用法：
#   ci-discover.sh [選項]
#
# 選項：
#   --project-dir <dir>   要導入 CI 的專案目錄（預設：目前目錄）
#   --servers <path>      使用者層 servers.yml（預設：${CI_TOOLS_SERVERS}，
#                         否則 ${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml）
#   --search-root <dir>   額外掃描這個目錄下（深度 3）的 azure-pipelines.yml 與 .ci-tools.yml；可重複
#   --ssh-dir <dir>       SSH 金鑰目錄（預設：$HOME/.ssh）
#   --no-network          不發任何網路請求（只看本機）
#   -h, --help            顯示說明
#
# 結束碼：0 探索完成（不論找到多少）；1 參數錯誤

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/lib/ci_config.py"

PROJECT_DIR="$PWD"
SERVERS="${CI_TOOLS_SERVERS:-${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml}"
SSH_DIR="$HOME/.ssh"
NETWORK=1
SEARCH_ROOTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --project-dir) PROJECT_DIR="${2:-}"; shift 2 ;;
    --servers) SERVERS="${2:-}"; shift 2 ;;
    --search-root) SEARCH_ROOTS+=("${2:-}"); shift 2 ;;
    --ssh-dir) SSH_DIR="${2:-}"; shift 2 ;;
    --no-network) NETWORK=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

[ -d "$PROJECT_DIR" ] || { echo "錯誤：專案目錄不存在：$PROJECT_DIR" >&2; exit 1; }
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
command -v python3 >/dev/null 2>&1 || { echo "錯誤：需要 python3（讀設定檔用）" >&2; exit 1; }

cfg() { python3 "$CFG" "$@"; }
section() { printf '\n== %s\n' "$1"; }

# 收集建議，最後統一輸出
ADVICE=()
advise() { ADVICE+=("$1"); }

# ---------------------------------------------------------------- 1. 使用者層 servers.yml
section "使用者層設定（servers.yml）"
SERVER_ROWS=""
if [ -f "$SERVERS" ]; then
  echo "位置：$SERVERS"
  if SERVER_ROWS="$(cfg servers "$SERVERS" 2>&1)"; then
    if [ -z "$SERVER_ROWS" ]; then
      echo "  （檔案存在，但沒有任何 server）"
    fi
    while IFS=$'\x1f' read -r name url coll pat_env key _ssh_url _api; do
      [ -n "$name" ] || continue
      if [ -n "${!pat_env:-}" ]; then pat_state="已設定"; else pat_state="未設定"; fi
      if [ -z "$key" ]; then key_state="未指定"; elif [ -f "$key" ]; then key_state="存在"; else key_state="找不到檔案"; fi
      echo "  server ${name}：$url/$coll"
      echo "    權杖環境變數 ${pat_env}：${pat_state}（不顯示值）"
      echo "    SSH 金鑰 $(basename "${key:-無}")：$key_state"
      agents="$(cfg agents "$SERVERS" "$name" 2>/dev/null || true)"
      while IFS=$'\x1f' read -r aname gh npm gv _jdk; do
        [ -n "$aname" ] || continue
        echo "    agent ${aname}：gradle_user_home=${gh:-未填} npm_cache=${npm:-未填} 已快取 Gradle=${gv:-未填}"
      done <<<"$agents"
    done <<<"$SERVER_ROWS"
  else
    echo "  讀取失敗：$SERVER_ROWS"
    SERVER_ROWS=""
  fi
else
  echo "找不到：$SERVERS"
  echo "  （可參考 references/servers.example.yml 建立；探索結果可以幫你填）"
fi

# ---------------------------------------------------------------- 2. 本專案
section "本專案（${PROJECT_DIR}）"
PROJ_CFG="$PROJECT_DIR/.ci-tools.yml"
ADO_PROJECT=""
if [ -f "$PROJ_CFG" ]; then
  echo "  .ci-tools.yml：存在"
  ADO_PROJECT="$(cfg get "$PROJ_CFG" ado_project 2>/dev/null || true)"
  if ! cfg get "$PROJ_CFG" repos >/dev/null 2>&1; then
    echo "  （舊版單層格式：沒有 repos 清單。可依 references/ci-tools.example.yml 改成兩層設定）"
    if [ -z "$ADO_PROJECT" ]; then
      # 舊格式沒有 ado_project：從 ado_url 的 .../<專案>/_git/<repo> 取出專案名稱
      ADO_PROJECT="$(cfg get "$PROJ_CFG" ado_url 2>/dev/null | sed -n 's#.*/\([^/]*\)/_git/.*#\1#p')"
    fi
  fi
  [ -n "$ADO_PROJECT" ] && echo "  ADO 專案：${ADO_PROJECT}"
else
  echo "  .ci-tools.yml：沒有"
fi
if [ -f "$PROJECT_DIR/azure-pipelines.yml" ]; then echo "  azure-pipelines.yml：存在"; else echo "  azure-pipelines.yml：沒有"; fi
WRAPPER="$PROJECT_DIR/gradle/wrapper/gradle-wrapper.properties"
PROJECT_GRADLE=""
if [ -f "$WRAPPER" ]; then
  PROJECT_GRADLE="$(sed -n 's/.*gradle-\([0-9][0-9.]*[0-9]\)-[a-z]*\.zip.*/\1/p' "$WRAPPER" | head -n1)"
  echo "  Gradle wrapper 版本：${PROJECT_GRADLE:-讀不出來}"
fi

# ---------------------------------------------------------------- 2a／2b 共用的掃描程式（python，只讀）
# 以 python3 -c 執行、不用 heredoc：bash 5.1 以前 heredoc 會先寫成 /tmp 暫存檔，違反「只讀」。
# 因為整段放在 shell 單引號裡，程式內不得出現單引號字元（需要時用 \x27）。
# 模式：
#   tests <專案目錄> [ADO 主機網址...]  測試設定與測試程式碼裡的連線字串、會啟動 Spring context 的測試類
#   xrepo <專案目錄>                     前端檔的相對引用是否跳出 git repo 根目錄
# 輸出：給人看的行（已縮排兩格），最後一行是「@@HITS=<件數>」供 shell 判斷。
# 安全：連線字串只輸出 scheme、主機、埠；帳號、密碼、其餘參數一律不輸出。
SCAN_PY='
import os, re, stat, sys, time
from urllib.parse import urlsplit

MAX_DEPTH = 12            # 目錄深度上限（Java 測試套件路徑常見 9～11 層）
MAX_SIZE = 1024 * 1024    # 單檔超過 1MB 跳過
TIME_BUDGET = 8.0         # 秒；超過就停止並註明結果不完整
PRUNE = {"node_modules", ".git", "build", "target", "dist", ".gradle", ".idea",
         "coverage", ".next", ".nuxt", "storybook-static", ".svn"}

mode, project = sys.argv[1], os.path.abspath(sys.argv[2])
start = time.monotonic()
timed_out = False

def walk(root):
    global timed_out
    base = root.rstrip(os.sep).count(os.sep)
    for d, dirs, files in os.walk(root):
        if time.monotonic() - start > TIME_BUDGET:
            timed_out = True
            return
        depth = d.count(os.sep) - base
        dirs[:] = sorted(x for x in dirs if x not in PRUNE and not os.path.islink(os.path.join(d, x)))
        if depth >= MAX_DEPTH:
            dirs[:] = []
        for f in sorted(files):
            p = os.path.join(d, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_size <= MAX_SIZE:
                yield p

def read(p):
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None

def keep_newlines(text):
    return "\n" * text.count("\n")

# 剝除註解（保留換行，行號才對得上）
C_TOKENS = re.compile(
    r"(//[^\n]*)"
    r"|(/\*[\s\S]*?(?:\*/|\Z))"
    r"|(\"\"\"[\s\S]*?(?:\"\"\"|\Z))"
    r"|(\"(?:\\.|[^\"\\\n])*\"?)"
    r"|(\x27(?:\\.|[^\x27\\\n])*\x27?)"
    r"|(`(?:\\.|[^`\\])*`?)")

def strip_c(text):
    def repl(m):
        if m.group(1) is not None:
            return ""
        if m.group(2) is not None:
            return keep_newlines(m.group(2))
        return m.group(0)
    return C_TOKENS.sub(repl, text)

def strip_xml(text):
    return re.sub(r"<!--[\s\S]*?(?:-->|\Z)", lambda m: keep_newlines(m.group(0)), text)

def strip_props(text):
    out = []
    for line in text.split("\n"):
        s = line.lstrip()
        if s[:1] in ("#", ";", "!"):
            out.append("")
        else:
            # Java .properties 的跳脫寫法：jdbc\:mysql\://h\:3306 還原成 jdbc:mysql://h:3306
            for a, b in (("\\:", ":"), ("\\=", "="), ("\\#", "#"), ("\\!", "!")):
                line = line.replace(a, b)
            out.append(line)
    return "\n".join(out)

def strip_yaml(text):
    out = []
    for line in text.split("\n"):
        if line.lstrip().startswith("#"):
            out.append("")
        else:
            out.append(re.sub(r"\s#.*$", "", line))
    return "\n".join(out)

def strip_by_ext(path, text):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".java", ".kt", ".groovy", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx",
               ".vue", ".svelte", ".scss", ".sass", ".less", ".css"):
        text = strip_c(text)
        if ext in (".vue", ".svelte"):
            text = strip_xml(text)
        return text
    if ext == ".xml":
        return strip_xml(text)
    if ext in (".yml", ".yaml"):
        return strip_yaml(text)
    if ext == ".json":
        return text
    return strip_props(text)

def line_of(text, pos):
    return text.count("\n", 0, pos) + 1

def rel(p):
    return os.path.relpath(p, project)

def finish(hits):
    if timed_out:
        print("  （掃描超過 %d 秒已停止，以上結果不完整）" % TIME_BUDGET)
    print("@@HITS=%d" % hits)

# ---------------------------------------------------------------- tests 模式
CONN = re.compile(
    r"(?<![A-Za-z0-9+])(jdbc:[A-Za-z0-9]+(?::[A-Za-z0-9]+)*|mongodb(?:\+srv)?|rediss?|amqps?|mysql|mariadb|postgres(?:ql)?)"
    r"://", re.I)
ORACLE = re.compile(r"(?<![A-Za-z0-9])jdbc:oracle:thin:", re.I)
HOST = re.compile(r"(\$\{[^}]*\}|\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(?::(\d{1,5}|\$\{[^}]*\}))?")
# 主機:埠 之後必須緊接這些分隔字元之一（或字串結尾），否則視為無法解析
DELIMS = set("/;?,#&\"\x27<>)`\\ \t")
UNPARSED = "<無法解析>"
SKIP_SCHEME = ("jdbc:h2:mem", "jdbc:hsqldb:mem", "jdbc:derby:memory", "jdbc:tc:", "jdbc:sqlite")
# 只認註解用法（不認 import 行），行號才會指到類別上的註解
SPRING = re.compile(r"@(?:[\w.]+\.)?(?:SpringBootTest|ContextConfiguration)\b"
                    r"|@(?:[\w.]+\.)?RunWith\s*\(\s*(?:[\w.]+\.)?Spring(?:JUnit4ClassRunner|Runner)\b"
                    r"|@(?:[\w.]+\.)?ExtendWith\s*\(\s*(?:[\w.]+\.)?SpringExtension\b")
IGNORED = re.compile(r"@(?:[\w.]+\.)?(?:Ignore|Disabled)\b")
CLASS_DECL = re.compile(r"^[ \t]*(?:(?:public|protected|private|abstract|final|open|internal|static|sealed)\s+)*class\s+(\w+)", re.M)
TEST_EXT = (".properties", ".yml", ".yaml", ".xml", ".java", ".kt", ".groovy", ".conf", ".cfg", ".ini", ".json")
MAIN_TEST_EXT = (".properties", ".yml", ".yaml", ".xml")
CODE_EXT = (".java", ".kt", ".groovy")

def parse_host(candidate, extra_end=""):
    # candidate 開頭必須是「主機[:埠]」且後面緊接分隔字元；空的視為沒有主機
    if not candidate or candidate[0] in DELIMS:
        return "", ""
    m = HOST.match(candidate)
    if not m:
        return UNPARSED, ""
    after = candidate[m.end():]
    if after and after[0] not in DELIMS and after[0] not in extra_end:
        return UNPARSED, ""
    return m.group(1), m.group(2) or ""

def host_port(rest):
    # rest：scheme:// 之後到行尾的原文。帳密安全優先，無法確定就回 UNPARSED。
    # 帳號、密碼可能含任何字元（; ? ) 引號 空白 : / # @），所以：
    #   - 整段沒有 @ → 沒有帳密，直接解析開頭的主機:埠
    #   - authority（到第一個分隔字元為止）之後還有 @ → 分不出 @ 屬於密碼還是後面的內容，UNPARSED
    #   - @ 全部在 authority 內 → 以最後一個 @ 切掉「帳號:密碼」
    if "@" not in rest:
        return parse_host(rest)
    k = next((i for i, ch in enumerate(rest) if ch in DELIMS), len(rest))
    if "@" in rest[k:]:
        return UNPARSED, ""
    return parse_host(rest[:k].rsplit("@", 1)[1])

def oracle_host(rest):
    # rest：jdbc:oracle:thin: 之後到行尾。格式「[帳號/密碼]@[//]主機:埠…」；@ 不是恰好一個就 UNPARSED
    if rest.count("@") != 1:
        return UNPARSED, ""
    after = rest.split("@", 1)[1]
    if after.startswith("//"):
        after = after[2:]
    return parse_host(after, ":")   # oracle 舊格式「主機:埠:SID」

def rest_of_line(text, pos):
    end = text.find("\n", pos)
    return text[pos:] if end < 0 else text[pos:end]

def ado_hosts(urls):
    names = set()
    for u in urls:
        try:
            h = urlsplit(u).hostname
        except ValueError:
            h = None
        if h:
            names.add(h.lower())
    # 只讀 /etc/hosts 補上「名稱 ↔ IP」對應（不查 DNS，不發網路）
    alias = {}
    try:
        with open("/etc/hosts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split("#", 1)[0].split()
                if len(parts) >= 2:
                    group = [x.lower() for x in parts]
                    for x in group:
                        alias.setdefault(x, set()).update(group)
    except OSError:
        pass
    expanded = set(names)
    for n in names:
        expanded |= alias.get(n, set())
    return expanded

def classify(host, ado):
    h = host.lower().strip("[]")
    if not h:
        return "未指定主機"
    if host == UNPARSED:
        return "無法安全分辨主機與帳密，請人工確認這一行"
    if h.startswith("${"):
        return "由變數決定，CI 上的實際值要另外確認"
    if h in ado:
        return "⚠️ 與 ADO server 同機：CI 跑測試會直接動到這台機器上的真實服務"
    if h in ("localhost", "0.0.0.0", "::1") or h.startswith("127."):
        return "CI 上會連到 agent 本機"
    return "外部主機：CI agent 連得到就會真的連線"

def is_test_target(r):
    rr = "/" + r.replace(os.sep, "/")
    ext = os.path.splitext(r)[1].lower()
    if "/src/test/" in rr:
        return ext in TEST_EXT
    if "/src/main/resources/" in rr:
        return ext in MAIN_TEST_EXT and "test" in os.path.basename(r).lower()
    return False

def run_tests(urls):
    ado = ado_hosts(urls)
    conns, springs, seen = [], [], set()
    for p in walk(project):
        r = rel(p)
        if not is_test_target(r):
            continue
        raw = read(p)
        if raw is None:
            continue
        text = strip_by_ext(p, raw)
        found = []
        for m in CONN.finditer(text):
            scheme = m.group(1)
            if scheme.lower().startswith(SKIP_SCHEME):
                continue
            host, port = host_port(rest_of_line(text, m.end()))
            found.append((m.start(), scheme + "://", host, port))
        for m in ORACLE.finditer(text):
            host, port = oracle_host(rest_of_line(text, m.end()))
            found.append((m.start(), "jdbc:oracle:thin:@", host, port))
        for pos, scheme, host, port in found:
            key = (r, line_of(text, pos), scheme.lower(), host.lower(), port)
            if key in seen:
                continue
            seen.add(key)
            shown = scheme + (host or "（無主機）") + ((":" + port) if port else "")
            conns.append("  %s:%d  %s  ← %s" % (r, key[1], shown, classify(host, ado)))
        if os.path.splitext(p)[1].lower() in CODE_EXT and "/src/test/" in "/" + r.replace(os.sep, "/"):
            m = SPRING.search(text)
            if m:
                c = CLASS_DECL.search(text)
                head = text[:c.start()] if c else text
                if not IGNORED.search(head):
                    name = c.group(1) if c else os.path.splitext(os.path.basename(p))[0]
                    springs.append("  %s:%d  %s  ← 會啟動 Spring context，可能連外" % (r, line_of(text, m.start()), name))
    print("  連線字串（已剝除註解；記憶體資料庫不列；只顯示 scheme、主機、埠）：")
    print("\n".join(conns) if conns else "    （沒有找到）")
    print("  會啟動 Spring context 的測試類（類別上沒有 @Ignore／@Disabled）：")
    print("\n".join(springs) if springs else "    （沒有找到）")
    finish(len(conns) + len(springs))

# ---------------------------------------------------------------- xrepo 模式
FRONT_EXT = (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".vue", ".svelte", ".scss", ".sass", ".less", ".css")
IMPORT = re.compile(
    r"(?:\bimport\s*(?:[\w$*{}\s,]{0,1000}?\s*\bfrom\s*)?"
    r"|\bexport\s+[\w$*{}\s,]{0,1000}?\s*\bfrom\s*"
    r"|\brequire\s*\(\s*"
    r"|\bimport\s*\(\s*"
    r"|@import\s+(?:url\(\s*)?"
    r"|@use\s+|@forward\s+)"
    r"([\"\x27])(\.{1,2}/[^\"\x27\n]*)\1")
_roots = {}

def git_root(d):
    chain = []
    cur = d
    while True:
        if cur in _roots:
            found = _roots[cur]
            break
        chain.append(cur)
        if os.path.exists(os.path.join(cur, ".git")):
            found = cur
            break
        parent = os.path.dirname(cur)
        if parent == cur:
            found = project
            break
        cur = parent
    for c in chain:
        _roots[c] = found
    return found

def run_xrepo():
    hits = []
    for p in walk(project):
        if os.path.splitext(p)[1].lower() not in FRONT_EXT:
            continue
        raw = read(p)
        if raw is None:
            continue
        text = strip_by_ext(p, raw)
        root = git_root(os.path.dirname(p))
        for m in IMPORT.finditer(text):
            spec = m.group(2)
            target = os.path.normpath(os.path.join(os.path.dirname(p), spec))
            if target == root or target.startswith(root + os.sep):
                continue
            outside = os.path.relpath(target, os.path.dirname(root)).split(os.sep)[0]
            where = ("repo 外的 %s" % outside) if outside != ".." else "repo 上層目錄之外"
            hits.append("  %s:%d  \"%s\" → %s（%s）" % (rel(p), line_of(text, m.start(2)), spec, target, where))
    print("\n".join(hits) if hits else "  （沒有找到）")
    finish(len(hits))

if mode == "tests":
    run_tests(sys.argv[3:])
elif mode == "xrepo":
    run_xrepo()
else:
    sys.exit("未知模式：" + mode)
'
scan() { python3 -c "$SCAN_PY" "$@"; }
# 執行掃描並拆出件數：輸出存進 SCAN_OUT、件數存進 SCAN_HITS（掃描失敗時件數為 0 並印出原因）
run_scan() {
  local out
  if out="$(scan "$@" 2>&1)"; then
    SCAN_HITS="$(printf '%s\n' "$out" | sed -n 's/^@@HITS=\([0-9]*\)$/\1/p' | tail -n1)"
    SCAN_OUT="$(printf '%s\n' "$out" | sed '/^@@HITS=/d')"
  else
    SCAN_HITS=0
    SCAN_OUT="  （掃描失敗：$(printf '%s' "$out" | tail -n1)）"
  fi
  SCAN_HITS="${SCAN_HITS:-0}"
}

# ---------------------------------------------------------------- 2a. 測試外部連線
section "測試外部連線（套範本前先確認 CI 跑測試會連到哪裡；只讀、不連線）"
ADO_URLS=()
while IFS=$'\x1f' read -r name url _coll _pat _key ssh_url _api; do
  [ -n "$name" ] || continue
  ADO_URLS+=("$url" "$ssh_url")
done <<<"$SERVER_ROWS"
run_scan tests "$PROJECT_DIR" "${ADO_URLS[@]+"${ADO_URLS[@]}"}"
printf '%s\n' "$SCAN_OUT"
if [ "$SCAN_HITS" -gt 0 ]; then
  TEST_TIP="第一次建 CI 建議先只建置（-DskipTests／gradle assemble），確認測試不連真實環境再開測試。"
  echo "  建議：$TEST_TIP"
  advise "測試會連外（見「測試外部連線」）→ $TEST_TIP"
fi

# ---------------------------------------------------------------- 2b. 跨 repo 相對引用
section "跨 repo 相對引用（前端檔的 import／require／@import 跳出 git repo 根目錄）"
run_scan xrepo "$PROJECT_DIR"
printf '%s\n' "$SCAN_OUT"
if [ "$SCAN_HITS" -gt 0 ]; then
  XREPO_TIP="CI 需並排 checkout 該 repo（resources.repositories），並注意被引用處自身的 npm 相依。"
  echo "  建議：$XREPO_TIP"
  advise "前端有跨 repo 相對引用（見「跨 repo 相對引用」）→ $XREPO_TIP"
fi

# ---------------------------------------------------------------- 3. 其他專案的 CI 設定
section "其他專案的 CI 設定（同層目錄與 --search-root）"
FOUND_OTHERS=0
ROOTS=("$(dirname "$PROJECT_DIR")")
if [ "${#SEARCH_ROOTS[@]}" -gt 0 ]; then ROOTS+=("${SEARCH_ROOTS[@]}"); fi
SEEN=""
for root in "${ROOTS[@]}"; do
  [ -d "$root" ] || { echo "  （略過不存在的目錄：${root}）"; continue; }
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    case "$f" in "$PROJECT_DIR"/*) continue ;; esac
    case "$SEEN" in *"|$f|"*) continue ;; esac
    SEEN="$SEEN|$f|"
    FOUND_OTHERS=$((FOUND_OTHERS + 1))
    echo "  $f"
    if [ "$(basename "$f")" = azure-pipelines.yml ]; then
      # 列出引用的 ADO 專案／repo（resources.repositories 的 name: 專案/repo）與 timeout
      refs="$(sed -n 's/^[[:space:]]*name:[[:space:]]*\([^[:space:]#]*\/[^[:space:]#]*\).*/\1/p' "$f" | sort -u | tr '\n' ' ')"
      [ -n "$refs" ] && echo "    引用的 ADO repo：$refs"
      # 可直接搬進 servers.yml 的 agent 路徑（variables 清單裡 name: X 的下一行 value: Y）
      awk '/name:[[:space:]]*(JAVA_HOME|GRADLE_USER_HOME|NPM_CONFIG_CACHE)[[:space:]]*$/ { n = $NF; next }
           n != "" && /value:/ { sub(/^[[:space:]]*value:[[:space:]]*/, ""); print "    " n " = " $0 }
           { n = "" }' "$f"
      if grep -q 'timeoutInMinutes' "$f"; then echo "    有設定 timeoutInMinutes"; fi
    fi
  done < <(find "$root" -maxdepth 3 \( -name node_modules -o -name .git -o -name build \) -prune -o \
             -type f \( -name azure-pipelines.yml -o -name .ci-tools.yml \) -print 2>/dev/null | sort)
done
[ "$FOUND_OTHERS" -eq 0 ] && echo "  （沒有找到）"

# ---------------------------------------------------------------- 4. SSH 金鑰
section "SSH 金鑰（${SSH_DIR}；只看 .pub 判斷型別，不讀私鑰內容）"
RSA_KEYS=0
if [ -d "$SSH_DIR" ]; then
  for pub in "$SSH_DIR"/*.pub; do
    [ -f "$pub" ] || continue
    priv="${pub%.pub}"
    [ -f "$priv" ] || continue
    info="$(ssh-keygen -l -f "$pub" 2>/dev/null || true)"
    bits="${info%% *}"
    type="$(printf '%s' "$info" | sed -n 's/.*(\([A-Z0-9-]*\))$/\1/p')"
    mark=""
    if [ "$type" = RSA ]; then mark="  ← ADO 內建 SSH 可用（踩坑 4）"; RSA_KEYS=$((RSA_KEYS + 1)); fi
    echo "  $(basename "$priv")：${type:-未知} ${bits:-?} bits$mark"
  done
  for priv in "$SSH_DIR"/id_*; do
    [ -f "$priv" ] || continue
    case "$priv" in *.pub) continue ;; esac
    [ -f "$priv.pub" ] || echo "  $(basename "$priv")：型別未知（沒有 .pub，不讀私鑰）"
  done
else
  echo "  （目錄不存在）"
fi

# ---------------------------------------------------------------- 5. git 平台判斷
section "git 平台判斷（看 API 回應，不看主機名，踩坑 31）"
HOSTS=()
add_host() {
  local u="$1" base=""
  case "$u" in
    http://*|https://*) base="$(printf '%s' "$u" | sed -E 's#^(https?://[^/]+).*#\1#')" ;;
    ssh://*) base="https://$(printf '%s' "$u" | sed -E 's#^ssh://([^@/]*@)?([^:/]+).*#\2#')" ;;
    *@*:*) base="https://$(printf '%s' "$u" | sed -E 's#^[^@]*@([^:]+):.*#\1#')" ;;
  esac
  [ -n "$base" ] || return 0
  local h
  for h in "${HOSTS[@]+"${HOSTS[@]}"}"; do [ "$h" = "$base" ] && return 0; done
  HOSTS+=("$base")
}
if [ -f "$PROJ_CFG" ]; then
  while IFS= read -r u; do [ -n "$u" ] && add_host "$u"; done < <(cfg git-urls "$PROJ_CFG" 2>/dev/null || true)
fi
origin="$(git -C "$PROJECT_DIR" remote get-url origin 2>/dev/null || true)"
[ -n "$origin" ] && add_host "$origin"

GITEA_FOUND=0
if [ "${#HOSTS[@]}" -eq 0 ]; then
  echo "  （沒有可判斷的 git 主機：.ci-tools.yml 沒有 gitea_url，專案也沒有 origin）"
elif [ "$NETWORK" -eq 0 ]; then
  for h in "${HOSTS[@]}"; do echo "  ${h}：略過（--no-network）"; done
else
  probe() {
    local code
    code="$(curl -s -m 5 -o "$1" -w '%{http_code}' "$2" 2>/dev/null)" || true
    echo "${code:-000}"
  }
  TMPD="$(mktemp -d)"
  for h in "${HOSTS[@]}"; do
    c1="$(probe "$TMPD/v1" "$h/api/v1/version")"
    if [ "$c1" = 000 ]; then echo "  ${h}：連不上（略過）"; continue; fi
    if [ "$c1" = 200 ] && grep -q '"version"' "$TMPD/v1"; then
      v="$(sed -n 's/.*"version":"\([^"]*\)".*/\1/p' "$TMPD/v1")"
      echo "  ${h}：Gitea ${v}（/api/v1/version 回 200）"; GITEA_FOUND=1; continue
    fi
    c4="$(probe "$TMPD/v4" "$h/api/v4/version")"
    if { [ "$c4" = 200 ] || [ "$c4" = 401 ]; } && grep -q '^{' "$TMPD/v4"; then
      echo "  ${h}：GitLab（/api/v4/version 回 $c4 JSON）"; continue
    fi
    if grep -q '^{' "$TMPD/v1"; then
      echo "  ${h}：推定為 Gitea（/api/v1/version 回 $c1 JSON 訊息，多半是需要登入）"; GITEA_FOUND=1; continue
    fi
    echo "  ${h}：無法判斷（v1=${c1}、v4=${c4}）"
  done
fi

# ---------------------------------------------------------------- 6. ADO 查詢（只 GET）
section "ADO 現況（只發 GET）"
ADO_PROJECTS=""
if [ -z "$SERVER_ROWS" ]; then
  echo "  （沒有 servers.yml 或沒有 server，略過）"
elif [ "$NETWORK" -eq 0 ]; then
  echo "  （--no-network，略過）"
else
  while IFS=$'\x1f' read -r name url coll pat_env _key _ssh_url api; do
    [ -n "$name" ] || continue
    if [ -z "${!pat_env:-}" ]; then
      echo "  server ${name}：環境變數 $pat_env 未設定，略過查詢"; continue
    fi
    base="${url%/}/$coll"
    get() {
      # 權杖以 curl 設定檔從 stdin 傳入，不出現在指令列（ps 看不到）
      local out
      out="$(printf 'user = ":%s"\n' "${!pat_env}" | curl -s -m 10 -K - -w '\n%{http_code}' "$1" 2>/dev/null)" || true
      case "$out" in *$'\n'*) printf '%s' "$out" ;; *) printf '\n000' ;; esac
    }
    resp="$(get "$base/_apis/projects?api-version=$api")"
    code="${resp##*$'\n'}"; body="${resp%$'\n'*}"
    case "$code" in
      200)
        ADO_PROJECTS="$(printf '%s' "$body" | python3 -c 'import json,sys; [print(p["name"]) for p in json.load(sys.stdin).get("value",[])]' 2>/dev/null || true)"
        echo "  server $name 的專案（$(printf '%s\n' "$ADO_PROJECTS" | sed '/^$/d' | wc -l | tr -d ' ') 個）：$(printf '%s' "$ADO_PROJECTS" | tr '\n' ' ')" ;;
      401) echo "  server ${name}：401。權杖範圍不足或 IIS 基本驗證未停用（踩坑 32、5）" ;;
      000) echo "  server ${name}：連不上 ${base}（略過）" ;;
      *) echo "  server ${name}：查詢專案回 $code" ;;
    esac
    [ "$code" = 200 ] || continue
    resp="$(get "$base/_apis/distributedtask/pools?api-version=$api")"
    code="${resp##*$'\n'}"; body="${resp%$'\n'*}"
    if [ "$code" = 200 ]; then
      pools="$(printf '%s' "$body" | python3 -c 'import json,sys; print(" ".join(p["name"] for p in json.load(sys.stdin).get("value",[])))' 2>/dev/null || true)"
      echo "  agent pools：$pools"
    else
      echo "  agent pools：回 ${code}（權杖可能沒有 Agent Pools 讀取權限）"
    fi
  done <<<"$SERVER_ROWS"
fi

# ---------------------------------------------------------------- 7. 建議
section "建議"
if [ -n "$SERVER_ROWS" ]; then
  advise "已有 server 設定 → 跳過「0. 評估」與第 1、2 章（安裝 Server 與 agent）。"
  if [ -n "$ADO_PROJECT" ] && [ -n "$ADO_PROJECTS" ]; then
    if printf '%s\n' "$ADO_PROJECTS" | grep -qx "$ADO_PROJECT"; then
      advise "ADO 專案 $ADO_PROJECT 已存在 → 直接進第 3.2 節（推送 repo）或第 4 章（套範本）。"
    else
      advise "ADO 上還沒有專案 $ADO_PROJECT → 照第 3.1 節建立（需要 Project and Team 讀寫管理權限）。"
    fi
  fi
  if [ -n "$PROJECT_GRADLE" ]; then
    cached="$(printf '%s\n' "$SERVER_ROWS" | while IFS=$'\x1f' read -r n _; do cfg agents "$SERVERS" "$n" 2>/dev/null; done | cut -d $'\x1f' -f4 | tr ',' '\n' | sort -u)"
    if printf '%s\n' "$cached" | grep -qx "$PROJECT_GRADLE"; then
      advise "Gradle $PROJECT_GRADLE 已在 agent 快取過 → 第一次建置可以離線。"
    else
      advise "Gradle $PROJECT_GRADLE 不在 servers.yml 的 cached_gradle_versions → 第一次建置取消 offline 連線跑一次（踩坑 25），成功後把版本補進 servers.yml。"
    fi
  fi
else
  if [ "$FOUND_OTHERS" -gt 0 ]; then
    advise "沒有 servers.yml，但找到其他專案的 CI 設定 → server 很可能已經存在。先從上列檔案整理出 servers.yml（照 references/servers.example.yml），不要直接重裝。"
  elif [ "$RSA_KEYS" -gt 0 ]; then
    advise "沒有 servers.yml，也沒找到其他專案的 CI 設定，但有 RSA 金鑰 → 先問使用者是否已經有 ADO server（可加 --search-root 擴大掃描），確定沒有才從「0. 評估」開始。"
  else
    advise "沒有找到任何既有設定 → 從「0. 評估」開始，再照第 1、2 章安裝 Server 與 agent。"
  fi
fi
if [ "$FOUND_OTHERS" -gt 0 ]; then
  advise "同一台 server 上其他專案的 azure-pipelines.yml 可直接參考（JDK 路徑、快取目錄、多 repo 寫法）。"
fi
if [ "$GITEA_FOUND" -eq 0 ] && [ "${#HOSTS[@]}" -gt 0 ] && [ "$NETWORK" -eq 1 ]; then
  advise "沒有確認到 Gitea → 第 6 章的同步做法以 Gitea 為前提，其他平台要先確認可行。"
fi
i=1
for a in "${ADVICE[@]}"; do echo "  $i. $a"; i=$((i + 1)); done
