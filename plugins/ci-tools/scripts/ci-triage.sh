#!/usr/bin/env bash
# ci-triage.sh — 首次完整建置失敗分類：從 ADO 建置 log 抽出失敗的測試類別，產生本機重跑與基準比對指令
#
# 對 ADO 只發 GET（timeline 與 log），不改任何東西。權杖只從環境變數讀，不會印出。
#
# 流程（SKILL.md「5.1 首次建置失敗分類」）：
#   1. 抓 timeline，找 result=failed 的步驟，下載那些步驟的 log
#   2. 萃取「<類別> > <方法> FAILED」，統計每個類別失敗幾個方法
#   3. 產出：失敗類別清單檔、本機重跑指令（RECENT_TESTS_FILE＋--init-script）、
#      與 baseline_branch 比對測試檔是否被本專案改過的 git 指令
#   判讀：本機也失敗＝產品既有問題；本機通過＝環境差異（Windows 路徑、缺 gitignore 的設定檔、
#         需要容器或外部服務）。兩者都寫進 ci-init.gradle 排除清單，每筆附原因與移除條件，另開單追蹤。
#
# 用法：
#   ci-triage.sh --build <buildId 或 buildNumber> [選項]
#   ci-triage.sh --log-file <log> [--log-file <log2> ...] [選項]     # 已下載的 log，不連網路
#
# 選項：
#   --build <id>          建置 id（數字）或建置編號（例：20260101.1）
#   --config <path>       專案層 .ci-tools.yml（預設：目前目錄的 .ci-tools.yml，若存在）
#   --servers <path>      使用者層 servers.yml（預設：${CI_TOOLS_SERVERS}，
#                         否則 ${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml）
#   --project <name>      ADO 專案名稱（預設：.ci-tools.yml 的 ado_project）
#   --log-file <path>     直接分析這個 log 檔（可重複）；有指定就不連 ADO
#   --repo-dir <dir>      本機 repo，用來把只有簡短類別名的失敗補成完整類別名（預設：目前目錄）
#   --baseline <ref>      產品基準分支或 tag（預設：.ci-tools.yml 的 baseline_branch）
#   --gradle-tasks <t>    本機重跑的 Gradle task（預設：.ci-tools.yml 的 gradle_tasks，否則 test）
#   --out <dir>           輸出目錄（預設：${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/triage/<build>）
#   -h, --help            顯示說明
#
# 輸出檔：
#   failed-classes.txt   失敗的測試類別（一行一個，可直接當 RECENT_TESTS_FILE）
#   failed-tests.tsv     類別<TAB>失敗方法數<TAB>方法清單
#   unresolved.txt       只有簡短類別名、在 --repo-dir 找不到或找到多個的類別（需人工確認）
#   commands.txt         本機重跑與基準比對的指令
#   logs/                從 ADO 下載的失敗步驟 log
#
# 結束碼：0 完成（含「沒有找到失敗」）；1 參數、設定或連線錯誤

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/lib/ci_config.py"

BUILD=""
CONFIG=""
SERVERS="${CI_TOOLS_SERVERS:-${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml}"
PROJECT=""
LOG_FILES=()
REPO_DIR="$PWD"
BASELINE=""
TASKS=""
OUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --build) BUILD="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --servers) SERVERS="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --log-file) LOG_FILES+=("${2:-}"); shift 2 ;;
    --repo-dir) REPO_DIR="${2:-}"; shift 2 ;;
    --baseline) BASELINE="${2:-}"; shift 2 ;;
    --gradle-tasks) TASKS="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "錯誤：需要 python3" >&2; exit 1; }
if [ -z "$BUILD" ] && [ "${#LOG_FILES[@]}" -eq 0 ]; then
  echo "錯誤：--build 或 --log-file 至少要給一個" >&2; exit 1
fi
if [ -z "$CONFIG" ] && [ -f "$PWD/.ci-tools.yml" ]; then CONFIG="$PWD/.ci-tools.yml"; fi
if [ -n "$CONFIG" ] && [ ! -f "$CONFIG" ]; then echo "錯誤：找不到 ${CONFIG}" >&2; exit 1; fi

conf() { [ -n "$CONFIG" ] && python3 "$CFG" get "$CONFIG" "$1" 2>/dev/null || true; }
[ -n "$PROJECT" ] || PROJECT="$(conf ado_project)"
[ -n "$BASELINE" ] || BASELINE="$(conf baseline_branch)"
[ -n "$TASKS" ] || TASKS="$(conf gradle_tasks)"
[ -n "$TASKS" ] || TASKS="test"

[ -n "$OUT" ] || OUT="${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/triage/${BUILD:-local}"
mkdir -p "$OUT/logs"
echo "輸出目錄：$OUT"

# ---------------------------------------------------------------- 1. 從 ADO 下載失敗步驟的 log
if [ "${#LOG_FILES[@]}" -eq 0 ]; then
  [ -n "$CONFIG" ] || { echo "錯誤：連 ADO 需要 --config（或目前目錄有 .ci-tools.yml）" >&2; exit 1; }
  [ -n "$PROJECT" ] || { echo "錯誤：不知道 ADO 專案名稱，請在 .ci-tools.yml 寫 ado_project 或給 --project" >&2; exit 1; }
  [ -f "$SERVERS" ] || { echo "錯誤：找不到 servers.yml：${SERVERS}" >&2; exit 1; }
  row="$(python3 "$CFG" project-server "$CONFIG" "$SERVERS")" || { echo "錯誤：.ci-tools.yml 沒有可用的 server" >&2; exit 1; }
  IFS=$'\x1f' read -r _name url coll pat_env _key _ssh api <<<"$row"
  [ -n "${!pat_env:-}" ] || { echo "錯誤：環境變數 $pat_env 未設定（servers.yml 的 pat_env）" >&2; exit 1; }
  base="${url%/}/$coll/$PROJECT/_apis/build"

  get() {
    # 權杖以 curl 設定檔從 stdin 傳入，不出現在指令列；HTTP 錯誤時結束碼非 0
    printf 'user = ":%s"\n' "${!pat_env}" | curl -sS -f -m 30 -K - "$1"
  }

  if [[ "$BUILD" == *.* ]]; then
    id="$(get "$base/builds?buildNumber=$BUILD&api-version=$api" | python3 -c 'import json,sys; v=json.load(sys.stdin).get("value",[]); print(v[0]["id"] if v else "")')" \
      || { echo "錯誤：查詢建置編號 $BUILD 失敗（401 見踩坑 32、5）" >&2; exit 1; }
    [ -n "$id" ] || { echo "錯誤：找不到建置編號 $BUILD" >&2; exit 1; }
    echo "建置編號 $BUILD → id $id"
    BUILD_ID="$id"
  else
    BUILD_ID="$BUILD"
  fi

  get "$base/builds/$BUILD_ID/timeline?api-version=$api" >| "$OUT/timeline.json" \
    || { echo "錯誤：取得 timeline 失敗（401 見踩坑 32、5）" >&2; exit 1; }
  python3 - "$OUT/timeline.json" >| "$OUT/failed-steps.tsv" <<'PY'
import json, sys
recs = json.load(open(sys.argv[1], encoding="utf-8")).get("records", [])
for r in recs:
    if r.get("result") == "failed" and r.get("type") == "Task" and r.get("log"):
        print(f'{r["log"]["id"]}\t{r.get("name", "")}')
PY
  if [ ! -s "$OUT/failed-steps.tsv" ]; then
    echo "timeline 沒有失敗的步驟（建置可能成功，或失敗發生在 job 層級，請看網頁）"
  fi
  while IFS=$'\t' read -r log_id step; do
    [ -n "$log_id" ] || continue
    f="$OUT/logs/${log_id}.log"
    echo "下載失敗步驟 log：${step}（log ${log_id}）"
    get "$base/builds/$BUILD_ID/logs/$log_id?api-version=$api" >| "$f" || { echo "錯誤：下載 log $log_id 失敗" >&2; exit 1; }
    LOG_FILES+=("$f")
  done < "$OUT/failed-steps.tsv"
fi

# ---------------------------------------------------------------- 2. 萃取失敗的測試
if [ "${#LOG_FILES[@]}" -eq 0 ]; then
  echo "沒有 log 可分析。"; exit 0
fi
for f in "${LOG_FILES[@]}"; do
  [ -f "$f" ] || { echo "錯誤：找不到 log 檔 ${f}" >&2; exit 1; }
done

REPO_FILES=""
if git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  REPO_FILES="$OUT/repo-files.txt"
  git -C "$REPO_DIR" ls-files -- '*.java' '*.kt' '*.groovy' >| "$REPO_FILES"
fi

python3 - "$OUT" "${REPO_FILES:-}" "${LOG_FILES[@]}" <<'PY'
import os, re, sys
from collections import OrderedDict

out, repo_files, logs = sys.argv[1], sys.argv[2], sys.argv[3:]
# ADO log 每行前面有時間戳（2026-01-01T00:00:00.0000000Z）；Gradle 失敗行：<類別> > <方法> FAILED
ts = re.compile("^\ufeff?" r"(?:\d{4}-\d{2}-\d{2}T[\d:.]+Z\s+)?")
failed = re.compile(r"^(?P<cls>[A-Za-z_$][\w$.]*)\s+>\s+(?P<rest>.+?)\s+FAILED\s*$")
summary = re.compile(r"(\d+) tests? completed, (\d+) failed(?:, (\d+) skipped)?")

classes = OrderedDict()
summaries = []
for path in logs:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = ts.sub("", raw.rstrip("\r\n"))
            m = failed.match(line)
            if m:
                # 巢狀類別（Outer > Inner > method）只取最外層類別；方法取最後一段
                method = m.group("rest").split(" > ")[-1]
                classes.setdefault(m.group("cls"), []).append(method)
                continue
            s = summary.search(line)
            if s:
                summaries.append(s.group(0))

paths = []
if repo_files and os.path.isfile(repo_files):
    paths = [p.strip() for p in open(repo_files, encoding="utf-8") if p.strip()]

def resolve(name):
    if "." in name:
        return name, None
    hits = [p for p in paths if re.search(r"(^|/)" + re.escape(name) + r"\.(java|kt|groovy)$", p)]
    fq = set()
    for p in hits:
        m = re.search(r"src/[^/]+/(?:java|kotlin|groovy)/(.+)\.(?:java|kt|groovy)$", p)
        if m:
            fq.add(m.group(1).replace("/", "."))
    if len(fq) == 1:
        return fq.pop(), None
    return name, ("找到多個：" + ", ".join(sorted(fq))) if fq else "找不到原始檔"

resolved = OrderedDict()
unresolved = []
for cls, methods in classes.items():
    fq, why = resolve(cls)
    if why:
        unresolved.append(f"{cls}\t{why}")
    resolved.setdefault(fq, []).extend(methods)

with open(os.path.join(out, "failed-classes.txt"), "w", encoding="utf-8") as fh:
    for c in resolved:
        fh.write(c + "\n")
with open(os.path.join(out, "failed-tests.tsv"), "w", encoding="utf-8") as fh:
    for c, ms in resolved.items():
        fh.write(f"{c}\t{len(ms)}\t{', '.join(OrderedDict.fromkeys(ms))}\n")
with open(os.path.join(out, "unresolved.txt"), "w", encoding="utf-8") as fh:
    for u in unresolved:
        fh.write(u + "\n")

total = sum(len(v) for v in resolved.values())
print(f"失敗類別 {len(resolved)} 個、失敗方法 {total} 個")
for s in summaries:
    print(f"  Gradle 摘要：{s}")
for c, ms in resolved.items():
    print(f"  {c}（{len(ms)}）")
if unresolved:
    print(f"  另有 {len(unresolved)} 個類別無法補成完整類別名，見 unresolved.txt")
PY

# ---------------------------------------------------------------- 3. 本機重跑與基準比對指令
CLASSES="$OUT/failed-classes.txt"
CMDS="$OUT/commands.txt"
if [ ! -s "$CLASSES" ]; then
  echo "log 裡沒有找到「<類別> > <方法> FAILED」。若建置仍失敗，失敗可能在編譯或其他步驟，請直接看 logs/。"
  : >| "$CMDS"
  exit 0
fi

{
  echo "# 1. 本機用與 CI 相同的 init script，只跑這些失敗的類別（macOS／Linux；Windows 改用 gradlew.bat 與 set）"
  echo "#    注意：若 ci/ci-init.gradle 的排除清單已經列了這些類別，先暫時拿掉再跑。"
  echo "RECENT_TESTS_FILE=$(printf '%q' "$CLASSES") ./gradlew $TASKS --continue --init-script ci/ci-init.gradle"
  echo
  echo "# 2. 與產品基準比對：這些測試檔是否被本專案改過（沒有輸出＝沒改過）"
  printf 'git diff --stat %s HEAD --' "${BASELINE:-<baseline_branch>}"
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    printf " ':(glob)**/%s.*'" "$(printf '%s' "$c" | tr . /)"
  done < "$CLASSES"
  echo
  echo
  echo "# 3. 判讀"
  echo "#    基準比對有差異          → 本專案改動造成，照一般 bug 修"
  echo "#    沒差異且本機也失敗      → 產品既有問題：寫進 ci-init.gradle 排除清單，附原因與移除條件，另開單"
  echo "#    沒差異但本機通過        → 環境差異（Windows 路徑、缺 gitignore 的設定檔、需要容器／外部服務）：同上"
} >| "$CMDS"

echo
echo "產出："
echo "  $CLASSES"
echo "  $OUT/failed-tests.tsv"
echo "  $CMDS"
echo
cat "$CMDS"
