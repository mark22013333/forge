#!/usr/bin/env bash
# ado-bootstrap-project.sh — 依 .ci-tools.yml 在 ADO server 上備好專案：專案、repo、程式碼、預設分支、pipeline、授權
#
# 用途：把專案接上新的（或換一台）ADO server。手動做時最花時間也最容易錯的幾步（建專案、
# 預設 repo 改名、改預設分支要用 repo id、pipeline 建立回 401 其實已建成、新專案要授權）一次照順序做完。
#
# 預設只預覽：只發 GET，逐步印出「現況 → 將要做什麼」。加 --apply 才寫入；每個寫入都會讀回確認，
# 不一致就以結束碼 2 結束。任一步失敗即停，不會跳到下一步。
# 權杖只從環境變數讀（servers.yml 的 pat_env），以 curl 設定檔從 stdin 傳入，不出現在指令列，也不會印出。
#
# 步驟：
#   1. 專案：不存在就建立（流程範本：.ci-tools.yml 的 ado_process，省略時用 server 的預設範本），輪詢到完成
#   2. repo：依 repos 清單；專案自帶的同名空 repo 改名給第一個 role: main 的 repo，其餘新建（踩坑 27、46）
#   3. 程式碼：預設不動，提示用 sync-all-repos.sh 推送；--import 時對空 repo 用 server 端匯入（未驗證）
#   4. 預設分支：改成 branches 的第一個（用 repo id，踩坑 18、27）
#   5. pipeline：main repo 有 /azure-pipelines.yml 且還沒有對應的 pipeline 就建立；建完重新列出確認（踩坑 34、36）。
#      pipelines API 回 500（yaml 不在 repo 預設分支）時，改用 build/definitions 指定分支建立（踩坑 58）
#   6. 授權：呼叫 ado-authorize.sh 授權 agent 佇列與依賴 repo（踩坑 35）；secure file 只提示，不自動處理（踩坑 45）
#   7. 環境一致性（只讀）：本機 azure-pipelines.yml 的 JAVA_HOME／GRADLE_USER_HOME／NPM_CONFIG_CACHE
#      與 servers.yml 的 agent 路徑比對；yaml 與 bin/*.sh 裡出現其他 server 的主機名就列出
#
# 用法：
#   ado-bootstrap-project.sh [--apply] [--import] [選項]
#
# 選項：
#   --apply               實際寫入（未帶時只預覽，只發 GET）
#   --import              對空的 repo 用 ADO server 端「匯入存放庫」從 Git 平台拉程式碼（**未在真實 server 驗證**）
#                         需要環境變數 GITEA_USER、GITEA_TOKEN（Git 平台的帳號與權杖，只經檔案傳給 ADO，不印出）
#                         來源網址用 repo 的 import_url，省略時用 gitea_url；必須是 http(s) 網址
#   --config <path>       專案層 .ci-tools.yml（預設：目前目錄的 .ci-tools.yml）
#   --servers <path>      使用者層 servers.yml（預設：${CI_TOOLS_SERVERS}，
#                         否則 ${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml）
#   --project <name>      ADO 專案名稱（預設：.ci-tools.yml 的 ado_project）
#   --poll <秒>           輪詢建立專案與匯入的間隔（預設 5）
#   --timeout <分>        建立專案或單一 repo 匯入最多等幾分鐘（預設 60）
#   -h, --help            顯示說明
#
# .ci-tools.yml 的可選欄位：ado_process（流程範本名稱，例 Basic）、agent_queue（預設 Default）
#
# 結束碼：0 完成（預覽沒有錯誤也是 0）；1 參數、設定或連線錯誤；2 寫入後讀回與預期不符，或匯入失敗

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/lib/ci_config.py"
AUTH="$HERE/ado-authorize.sh"

APPLY=0
IMPORT=0
CONFIG=""
SERVERS="${CI_TOOLS_SERVERS:-${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml}"
PROJECT=""
POLL=5
TIMEOUT_MIN=60

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --import) IMPORT=1; shift ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --servers) SERVERS="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --poll) POLL="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT_MIN="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "錯誤：需要 python3" >&2; exit 1; }
[[ "$POLL" =~ ^[0-9]+$ ]] || { echo "錯誤：--poll 要是整數秒" >&2; exit 1; }
[[ "$TIMEOUT_MIN" =~ ^[0-9]+$ ]] || { echo "錯誤：--timeout 要是整數分鐘" >&2; exit 1; }
if [ -z "$CONFIG" ] && [ -f "$PWD/.ci-tools.yml" ]; then CONFIG="$PWD/.ci-tools.yml"; fi
[ -n "$CONFIG" ] || { echo "錯誤：需要 --config（或目前目錄有 .ci-tools.yml）" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "錯誤：找不到 ${CONFIG}" >&2; exit 1; }
[ -f "$SERVERS" ] || { echo "錯誤：找不到 servers.yml：${SERVERS}" >&2; exit 1; }
if [ "$IMPORT" -eq 1 ]; then
  if [ -z "${GITEA_USER:-}" ] || [ -z "${GITEA_TOKEN:-}" ]; then
    echo "錯誤：--import 需要環境變數 GITEA_USER 與 GITEA_TOKEN（Git 平台的帳號與可讀取 repo 的權杖）。" >&2
    echo "  ADO server 會用它們從 Git 平台拉程式碼；腳本只把它們寫進暫時的 service endpoint，匯入後刪除。" >&2
    echo "  不想提供權杖時，不加 --import，改用 sync-all-repos.sh 從本機推送。" >&2
    exit 1
  fi
fi

[ -n "$PROJECT" ] || PROJECT="$(python3 "$CFG" get "$CONFIG" ado_project 2>/dev/null || true)"
[ -n "$PROJECT" ] || { echo "錯誤：不知道 ADO 專案名稱，請在 .ci-tools.yml 寫 ado_project 或給 --project" >&2; exit 1; }
row="$(python3 "$CFG" project-server "$CONFIG" "$SERVERS")" || { echo "錯誤：.ci-tools.yml 沒有可用的 server" >&2; exit 1; }
IFS=$'\x1f' read -r SERVER_NAME url coll pat_env _key _ssh api <<<"$row"
[ -n "${!pat_env:-}" ] || { echo "錯誤：環境變數 $pat_env 未設定（servers.yml 的 pat_env）" >&2; exit 1; }
[ -n "$api" ] || api="6.0"
PROCESS_NAME="$(python3 "$CFG" get "$CONFIG" ado_process 2>/dev/null || true)"
QUEUE="$(python3 "$CFG" get "$CONFIG" agent_queue 2>/dev/null || true)"
[ -n "$QUEUE" ] || QUEUE="Default"

ROOT="${url%/}/$coll"
BASE="$ROOT/$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$PROJECT")/_apis"
PIPE_API="6.0-preview.1"   # pipelines 端點在 Server 版要用 preview 版本
# server 端匯入（--import）用到的 API，版本取自官方 Azure DevOps Server 6.0 REST 文件：
#   https://learn.microsoft.com/en-us/rest/api/azure/devops/git/import-requests/create?view=azure-devops-server-rest-6.0
#   https://learn.microsoft.com/en-us/rest/api/azure/devops/serviceendpoint/endpoints/create?view=azure-devops-server-rest-6.0
#   https://learn.microsoft.com/en-us/rest/api/azure/devops/serviceendpoint/endpoints/delete?view=azure-devops-server-rest-6.0
IMPORT_API="6.0-preview.1"
EP_API="6.0-preview.4"
EP_TYPES_API="6.0-preview.1"

WORK="$(mktemp -d)"
RESP="$WORK/resp.json"
REPOS_JSON="$WORK/repos.json"
PIPES_TSV="$WORK/pipelines.tsv"
WARN=0

# req <METHOD> <URL> [body 檔]：回應寫入 ${RESP}，印出 HTTP 狀態碼（連線失敗印 000）
req() {
  local method="$1" u="$2" body="${3:-}" code
  local args=(-sS -m 60 -K - -o "$RESP" -w '%{http_code}' -X "$method")
  if [ -n "$body" ]; then args+=(-H 'Content-Type: application/json' --data-binary "@$body"); fi
  code="$(printf 'user = ":%s"\n' "${!pat_env}" | curl "${args[@]}" "$u" 2>/dev/null)" || code="000"
  printf '%s' "$code"
}

# http_fail <狀態碼> <動作說明>：印出錯誤與常見原因後結束（結束碼 1）
http_fail() {
  echo "錯誤：$2 失敗（HTTP $1）" >&2
  case "$1" in
    401) echo "  401：查詢也失敗多半是 IIS 基本驗證（踩坑 5）；只有寫入失敗多半是權杖範圍不足（踩坑 32，範圍見 SKILL.md）" >&2 ;;
    404) echo "  404：資源不存在，或權杖看不到它" >&2 ;;
    000) echo "  連不到 ADO（網址或網路）" >&2 ;;
  esac
  [ -s "$RESP" ] && head -c 400 "$RESP" | sed 's/^/  回應：/' >&2 && echo >&2
  exit 1
}
mismatch() { echo "錯誤：$1" >&2; exit 2; }
warn() { WARN=$((WARN + 1)); echo "  警告：$1"; }
plan() { if [ "$APPLY" -eq 1 ]; then echo "  → $1"; else echo "  [預覽] 將$1"; fi; }
urlenc() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
# pyjson '<python>' [參數...]：d 為 ${RESP} 的 JSON，參數在 sys.argv[2:]
pyjson() { python3 -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); $1" "$RESP" "${@:2}"; }
# mkbody <輸出檔> '<python 運算式>' [參數...]：把運算式（可用 a = sys.argv[2:]）的值寫成 JSON
mkbody() {
  local out="$1" expr="$2"; shift 2
  python3 -c "import json,sys,os; a=sys.argv[2:]; json.dump($expr, open(sys.argv[1],'w',encoding='utf-8'), ensure_ascii=False)" "$out" "$@"
}

# ---------------------------------------------------------------- 讀 repos 清單（含 ado_repo 與 import_url）
python3 - "$HERE/lib" "$CONFIG" >| "$WORK/repos.tsv" <<'PY' || { echo "錯誤：讀不到 .ci-tools.yml 的 repos" >&2; exit 1; }
import sys
sys.path.insert(0, sys.argv[1])
import ci_config as c
try:
    repos = c.load(sys.argv[2]).get("repos") or []
except c.ConfigError as exc:
    print(f"設定檔錯誤：{exc}", file=sys.stderr); sys.exit(1)
if not isinstance(repos, list):
    print("設定檔錯誤：repos 必須是清單", file=sys.stderr); sys.exit(1)
for r in repos:
    if not isinstance(r, dict) or not r.get("name"):
        print("設定檔錯誤：repos 的每一項都要有 name", file=sys.stderr); sys.exit(1)
    br = r.get("branches") or []
    if not isinstance(br, list): br = [br]
    print("\x1f".join([str(r["name"]), str(r.get("ado_repo") or r["name"]), str(r.get("role") or "main"),
                       str(r.get("import_url") or r.get("gitea_url") or ""), ",".join(str(b) for b in br)]))
PY
N_NAME=(); N_ADO=(); N_ROLE=(); N_SRC=(); N_BR=(); N_ID=(); N_REFS=(); N_PIPE=(); N_YAML=()
while IFS=$'\x1f' read -r n a r s b; do
  [ -n "$n" ] || continue
  N_NAME+=("$n"); N_ADO+=("$a"); N_ROLE+=("$r"); N_SRC+=("$s"); N_BR+=("$b"); N_ID+=(""); N_REFS+=(""); N_PIPE+=(""); N_YAML+=("")
done < "$WORK/repos.tsv"
[ "${#N_NAME[@]}" -gt 0 ] || { echo "錯誤：.ci-tools.yml 的 repos 清單是空的" >&2; exit 1; }
FIRST_MAIN=-1
for i in "${!N_NAME[@]}"; do
  if [ "${N_ROLE[$i]}" = main ]; then FIRST_MAIN=$i; break; fi
done

echo "server：${SERVER_NAME}（${ROOT}）  專案：${PROJECT}  模式：$([ "$APPLY" -eq 1 ] && echo 寫入 || echo 預覽)$([ "$IMPORT" -eq 1 ] && echo '＋匯入')"
echo

# ---------------------------------------------------------------- 1. 專案
echo "== 1. 專案"
PROJ_ID=""
code="$(req GET "$ROOT/_apis/projects/$(urlenc "$PROJECT")?api-version=$api")"
case "$code" in
  200) PROJ_ID="$(pyjson "print(d.get('id',''))")"; echo "  現況：已存在（id ${PROJ_ID}），沿用" ;;
  404) echo "  現況：不存在" ;;
  *) http_fail "$code" "查詢專案" ;;
esac
if [ -z "$PROJ_ID" ]; then
  code="$(req GET "$ROOT/_apis/process/processes?api-version=$api")"
  [ "$code" = 200 ] || http_fail "$code" "查詢流程範本"
  if [ -n "$PROCESS_NAME" ]; then
    TPL="$(pyjson "v=[p for p in d.get('value',[]) if (p.get('name') or '').lower()==sys.argv[2].lower()]; print(v[0]['id']+chr(31)+v[0]['name'] if v else '')" "$PROCESS_NAME")"
    if [ -z "$TPL" ]; then
      echo "錯誤：找不到流程範本「${PROCESS_NAME}」（.ci-tools.yml 的 ado_process）。現有：" >&2
      pyjson "[print('  ', p.get('name'), '（預設）' if p.get('isDefault') else '') for p in d.get('value',[])]" >&2
      exit 1
    fi
  else
    TPL="$(pyjson "v=[p for p in d.get('value',[]) if p.get('isDefault')]; print(v[0]['id']+chr(31)+v[0]['name'] if v else '')")"
    [ -n "$TPL" ] || { echo "錯誤：server 沒有標為預設的流程範本，請在 .ci-tools.yml 寫 ado_process" >&2; exit 1; }
  fi
  TPL_ID="${TPL%%$'\x1f'*}"; TPL_NAME="${TPL#*$'\x1f'}"
  plan "建立專案「${PROJECT}」（Git，流程範本 ${TPL_NAME}），輪詢建立進度"
  if [ "$APPLY" -eq 1 ]; then
    mkbody "$WORK/project.json" '{"name": a[0], "description": "CI 鏡像", "capabilities": {"versioncontrol": {"sourceControlType": "Git"}, "processTemplate": {"templateTypeId": a[1]}}}' "$PROJECT" "$TPL_ID"
    code="$(req POST "$ROOT/_apis/projects?api-version=$api" "$WORK/project.json")"
    case "$code" in 200|201|202) ;; *) http_fail "$code" "建立專案" ;; esac
    OP_ID="$(pyjson "print(d.get('id',''))")"
    [ -n "$OP_ID" ] || { echo "錯誤：建立專案的回應沒有 operation id" >&2; exit 1; }
    deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
    while :; do
      code="$(req GET "$ROOT/_apis/operations/$OP_ID?api-version=$api")"
      [ "$code" = 200 ] || http_fail "$code" "查詢建立專案的進度"
      st="$(pyjson "print(d.get('status',''))")"
      case "$st" in
        succeeded) break ;;
        failed|cancelled) mismatch "建立專案的 operation 結束狀態為 ${st}：$(pyjson "print(d.get('detailedMessage') or d.get('resultMessage') or '')")" ;;
      esac
      [ "$(date +%s)" -lt "$deadline" ] || mismatch "建立專案逾時（${TIMEOUT_MIN} 分鐘），最後狀態 ${st}"
      sleep "$POLL"
    done
    code="$(req GET "$ROOT/_apis/projects/$(urlenc "$PROJECT")?api-version=$api")"
    [ "$code" = 200 ] || mismatch "專案建立後讀回失敗（HTTP ${code}）"
    PROJ_ID="$(pyjson "print(d.get('id',''))")"
    echo "  已建立並讀回：專案 id ${PROJ_ID}"
  fi
fi
echo

# ---------------------------------------------------------------- 2. repo
echo "== 2. repo"
load_repos() {
  local code
  code="$(req GET "$BASE/git/repositories?api-version=$api")"
  [ "$code" = 200 ] || http_fail "$code" "列出 repo"
  cp "$RESP" "$REPOS_JSON"
}
# repo_field <名稱> <欄位>：從 repo 清單取欄位（名稱不分大小寫比對，ADO 的 repo 名稱不分大小寫）
repo_field() {
  python3 -c 'import json,sys; v=[r for r in json.load(open(sys.argv[1],encoding="utf-8")).get("value",[]) if r.get("name","").lower()==sys.argv[2].lower()]; print((v[0].get(sys.argv[3]) or "") if v else "")' "$REPOS_JSON" "$1" "$2"
}
# ref_count <repo id>：refs 數（0＝空 repo）
ref_count() {
  local code
  code="$(req GET "$BASE/git/repositories/$1/refs?api-version=$api")"
  [ "$code" = 200 ] || http_fail "$code" "查詢 repo 的 refs"
  pyjson "print(len(d.get('value',[])))"
}

if [ -n "$PROJ_ID" ]; then
  load_repos
  DEF_ID="$(repo_field "$PROJECT" id)"
  DEF_REFS=""; [ -n "$DEF_ID" ] && DEF_REFS="$(ref_count "$DEF_ID")"
else
  echo "  專案尚未建立：建立後會自帶一個與專案同名的空 repo（以下依此預估）"
  DEF_ID="(新專案的預設 repo)"; DEF_REFS=0
fi
# 預設 repo 本身就在清單內時（名稱相同），不改名
DEF_IN_LIST=0
for i in "${!N_NAME[@]}"; do
  if [ "$(printf '%s' "${N_ADO[$i]}" | tr '[:upper:]' '[:lower:]')" = "$(printf '%s' "$PROJECT" | tr '[:upper:]' '[:lower:]')" ]; then DEF_IN_LIST=1; fi
done

for i in "${!N_NAME[@]}"; do
  name="${N_ADO[$i]}"
  rid=""; [ -n "$PROJ_ID" ] && rid="$(repo_field "$name" id)"
  if [ -n "$rid" ]; then
    N_ID[$i]="$rid"; N_REFS[$i]="$(ref_count "$rid")"
    echo "  ${name}（${N_ROLE[$i]}）：已存在（id ${rid}，refs ${N_REFS[$i]}），沿用"
    continue
  fi
  if [ "$i" -eq "$FIRST_MAIN" ] && [ "$DEF_IN_LIST" -eq 0 ] && [ -n "$DEF_ID" ] && [ "$DEF_REFS" = 0 ]; then
    plan "把專案自帶的空 repo「${PROJECT}」改名為「${name}」（PATCH 用 repo id，踩坑 27）"
    if [ "$APPLY" -eq 1 ]; then
      mkbody "$WORK/rename.json" '{"name": a[0]}' "$name"
      code="$(req PATCH "$BASE/git/repositories/$DEF_ID?api-version=$api" "$WORK/rename.json")"
      [ "$code" = 200 ] || http_fail "$code" "repo 改名"
      code="$(req GET "$BASE/git/repositories/$DEF_ID?api-version=$api")"
      [ "$code" = 200 ] || mismatch "改名後讀回 repo 失敗（HTTP ${code}）"
      got="$(pyjson "print(d.get('name',''))")"
      [ "$got" = "$name" ] || mismatch "改名後讀回的名稱是「${got}」，預期「${name}」"
      N_ID[$i]="$DEF_ID"; N_REFS[$i]=0
      echo "    已改名並讀回：${got}（id ${DEF_ID}）"
    fi
    DEF_ID=""
    continue
  fi
  plan "建立 repo「${name}」（${N_ROLE[$i]}）"
  if [ "$APPLY" -eq 1 ]; then
    mkbody "$WORK/repo.json" '{"name": a[0], "project": {"id": a[1]}}' "$name" "$PROJ_ID"
    code="$(req POST "$BASE/git/repositories?api-version=$api" "$WORK/repo.json")"
    case "$code" in 200|201) ;; *) http_fail "$code" "建立 repo ${name}" ;; esac
    load_repos
    rid="$(repo_field "$name" id)"
    [ -n "$rid" ] || mismatch "建立 repo「${name}」後，repo 清單裡找不到它"
    N_ID[$i]="$rid"; N_REFS[$i]=0
    echo "    已建立並讀回：${name}（id ${rid}）"
  fi
done

if [ -n "$PROJ_ID" ]; then
  load_repos
  python3 - "$REPOS_JSON" >| "$WORK/repo-warn.txt" <<'PY'
import json, sys
names = [r.get("name", "") for r in json.load(open(sys.argv[1], encoding="utf-8")).get("value", [])]
for n in names:
    if n.lower().endswith(".git"):
        print(f"repo 名稱以 .git 結尾：{n}（多半是從 repo 下拉選單「匯入存放庫」多建出來的，踩坑 46）")
seen = {}
for n in names:
    seen.setdefault(n.lower(), []).append(n)
for k, v in seen.items():
    if len(v) > 1:
        print("repo 名稱重複（不分大小寫）：" + "、".join(v))
PY
  while IFS= read -r line; do [ -n "$line" ] && warn "$line"; done < "$WORK/repo-warn.txt"
  echo "  專案目前的 repo：$(python3 -c 'import json,sys; print("、".join(r.get("name","") for r in json.load(open(sys.argv[1],encoding="utf-8")).get("value",[])))' "$REPOS_JSON")"
fi
echo

# ---------------------------------------------------------------- 3. 程式碼
echo "== 3. 程式碼"
EP_ID=""
# 暫時的 service endpoint：成功、失敗或中途結束都要刪掉（裡面有 Git 平台的權杖）
cleanup_ep() {
  [ -n "$EP_ID" ] || return 0
  local id="$EP_ID" code
  EP_ID=""
  code="$(req DELETE "$ROOT/_apis/serviceendpoint/endpoints/$id?projectIds=$PROJ_ID&api-version=$EP_API")"
  case "$code" in
    200|204) echo "    已刪除暫時的 service endpoint（${id}）" ;;
    *) echo "  警告：刪除暫時的 service endpoint ${id} 失敗（HTTP ${code}），請到「專案設定 → Service connections」手動刪除" >&2 ;;
  esac
}
on_exit() { local rc=$?; cleanup_ep; exit "$rc"; }
trap on_exit EXIT

if [ "$IMPORT" -eq 0 ]; then
  echo "  不動程式碼。repo 備好後用下列指令推送 branches 指定的分支（依賴 repo 先推）："
  echo "    sync-all-repos.sh --config $CONFIG          # 預覽"
  echo "    sync-all-repos.sh --config $CONFIG --apply  # 推送"
else
  echo "  --import：ADO server 端匯入（未在真實 server 驗證）"
  TODO=()
  for i in "${!N_NAME[@]}"; do
    name="${N_ADO[$i]}"
    if [ -n "${N_REFS[$i]}" ] && [ "${N_REFS[$i]}" != 0 ]; then
      echo "  ${name}：已有內容（refs ${N_REFS[$i]}），跳過匯入"
      continue
    fi
    case "${N_SRC[$i]}" in
      http://*|https://*) ;;
      *) echo "錯誤：${name} 的來源網址不是 http(s)：「${N_SRC[$i]}」。server 端匯入要用 http(s) 網址，請在該 repo 加 import_url" >&2; exit 1 ;;
    esac
    TODO+=("$i")
    plan "建立暫時的 Git service endpoint → 匯入 ${N_SRC[$i]} 到 ${name} → 輪詢到完成 → 刪除 service endpoint"
  done
  if [ "${#TODO[@]}" -gt 0 ]; then
    # endpoint 型別名稱 git（網頁上的「其他 Git」）不在官方 REST 文件的範例裡，先向 server 確認它存在
    code="$(req GET "$ROOT/_apis/serviceendpoint/types?type=git&api-version=$EP_TYPES_API")"
    [ "$code" = 200 ] || http_fail "$code" "查詢 service endpoint 型別"
    has_git="$(pyjson "print(len([t for t in d.get('value',[]) if (t.get('name') or '').lower()=='git']))")"
    [ "$has_git" != 0 ] || { echo "錯誤：server 沒有 git 型別的 service endpoint，無法用 --import；改用 sync-all-repos.sh 推送" >&2; exit 1; }
  fi
  if [ "$APPLY" -eq 1 ]; then
    for i in ${TODO[@]+"${TODO[@]}"}; do
      name="${N_ADO[$i]}"; src="${N_SRC[$i]}"
      umask 077
      # 權杖只從環境變數讀進 body 檔，不出現在任何指令列
      python3 - "$WORK/ep.json" "$src" "$name" "$PROJ_ID" "$PROJECT" <<'PY'
import json, os, sys
out, src, name, pid, pname = sys.argv[1:6]
ep_name = "ci-tools-import-" + name
json.dump({
    "name": ep_name, "type": "git", "url": src, "isShared": False,
    "authorization": {"scheme": "UsernamePassword",
                      "parameters": {"username": os.environ["GITEA_USER"], "password": os.environ["GITEA_TOKEN"]}},
    "serviceEndpointProjectReferences": [{"projectReference": {"id": pid, "name": pname}, "name": ep_name}],
}, open(out, "w", encoding="utf-8"))
PY
      code="$(req POST "$ROOT/_apis/serviceendpoint/endpoints?api-version=$EP_API" "$WORK/ep.json")"
      : >| "$WORK/ep.json"   # 清空含權杖的 body 檔
      case "$code" in 200|201) ;; *) http_fail "$code" "建立暫時的 service endpoint" ;; esac
      EP_ID="$(pyjson "print(d.get('id',''))")"
      [ -n "$EP_ID" ] || { echo "錯誤：建立 service endpoint 的回應沒有 id" >&2; exit 1; }
      echo "    已建立暫時的 service endpoint（${EP_ID}）"
      mkbody "$WORK/import.json" '{"parameters": {"gitSource": {"url": a[0]}, "serviceEndpointId": a[1], "deleteServiceEndpointAfterImportIsDone": False}}' "$src" "$EP_ID"
      code="$(req POST "$BASE/git/repositories/${N_ID[$i]}/importRequests?api-version=$IMPORT_API" "$WORK/import.json")"
      case "$code" in 200|201|202) ;; *) http_fail "$code" "建立匯入要求（${name}）" ;; esac
      IR_ID="$(pyjson "print(d.get('importRequestId',''))")"
      [ -n "$IR_ID" ] || { echo "錯誤：匯入要求的回應沒有 importRequestId" >&2; exit 1; }
      deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
      last=""
      while :; do
        code="$(req GET "$BASE/git/repositories/${N_ID[$i]}/importRequests/$IR_ID?api-version=$IMPORT_API")"
        [ "$code" = 200 ] || http_fail "$code" "查詢匯入進度（${name}）"
        st="$(pyjson "print(d.get('status',''))")"
        if [ "$st" != "$last" ]; then echo "    $(date +%T) ${name} 匯入狀態：${st}"; last="$st"; fi
        case "$st" in
          completed) break ;;
          failed|abandoned)
            echo "錯誤：${name} 匯入 ${st}：$(pyjson "print((d.get('detailedStatus') or {}).get('errorMessage') or '')")" >&2
            exit 2 ;;
        esac
        [ "$(date +%s)" -lt "$deadline" ] || { echo "錯誤：${name} 匯入逾時（${TIMEOUT_MIN} 分鐘），最後狀態 ${st}" >&2; exit 2; }
        sleep "$POLL"
      done
      cleanup_ep
      N_REFS[$i]="$(ref_count "${N_ID[$i]}")"
      [ "${N_REFS[$i]}" != 0 ] || mismatch "${name} 匯入回報完成，但讀回 refs 數為 0"
      echo "    ${name} 匯入完成並讀回：refs ${N_REFS[$i]}"
    done
  fi
fi
echo

# ---------------------------------------------------------------- 4. 預設分支
echo "== 4. 預設分支"
for i in "${!N_NAME[@]}"; do
  name="${N_ADO[$i]}"; want="${N_BR[$i]%%,*}"
  if [ -z "$want" ]; then echo "  ${name}：.ci-tools.yml 沒寫 branches，跳過"; continue; fi
  if [ -z "${N_ID[$i]}" ] || [ "${N_REFS[$i]:-0}" = 0 ]; then
    echo "  ${name}：repo 還沒有內容，推送或匯入後再執行一次本腳本設定預設分支（目標 ${want}）"
    continue
  fi
  code="$(req GET "$BASE/git/repositories/${N_ID[$i]}/refs?filter=heads/$(urlenc "$want")&api-version=$api")"
  [ "$code" = 200 ] || http_fail "$code" "查詢分支 ${want}"
  has="$(pyjson "print(len([r for r in d.get('value',[]) if r.get('name')=='refs/heads/'+sys.argv[2]]))" "$want")"
  if [ "$has" = 0 ]; then echo "  ${name}：還沒有分支 ${want}，跳過（推上去後再執行一次）"; continue; fi
  code="$(req GET "$BASE/git/repositories/${N_ID[$i]}?api-version=$api")"
  [ "$code" = 200 ] || http_fail "$code" "查詢 repo ${name}"
  cur="$(pyjson "print(d.get('defaultBranch') or '')")"
  if [ "$cur" = "refs/heads/$want" ]; then echo "  ${name}：預設分支已是 ${want}"; continue; fi
  echo "  ${name}：預設分支目前是 ${cur:-（無）}"
  plan "把 ${name} 的預設分支改成 ${want}（PATCH 用 repo id，踩坑 18、27）"
  if [ "$APPLY" -eq 1 ]; then
    mkbody "$WORK/defbranch.json" '{"defaultBranch": "refs/heads/" + a[0]}' "$want"
    code="$(req PATCH "$BASE/git/repositories/${N_ID[$i]}?api-version=$api" "$WORK/defbranch.json")"
    [ "$code" = 200 ] || http_fail "$code" "修改預設分支（${name}）"
    code="$(req GET "$BASE/git/repositories/${N_ID[$i]}?api-version=$api")"
    [ "$code" = 200 ] || mismatch "修改預設分支後讀回失敗（HTTP ${code}）"
    got="$(pyjson "print(d.get('defaultBranch') or '')")"
    [ "$got" = "refs/heads/$want" ] || mismatch "${name} 的預設分支讀回是「${got}」，預期 refs/heads/${want}"
    echo "    已修改並讀回：${got}"
  fi
done
echo

# ---------------------------------------------------------------- 5. pipeline
echo "== 5. pipeline"
# load_pipes：列出所有 pipeline，逐條讀出 configuration，寫成「id<TAB>名稱<TAB>repo id<TAB>yaml 路徑」
load_pipes() {
  local code ids id
  : >| "$PIPES_TSV"
  code="$(req GET "$BASE/pipelines?api-version=$PIPE_API")"
  [ "$code" = 200 ] || http_fail "$code" "列出 pipeline"
  ids="$(pyjson "print(' '.join(str(p['id']) for p in d.get('value',[])))")"
  for id in $ids; do
    code="$(req GET "$BASE/pipelines/$id?api-version=$PIPE_API")"
    [ "$code" = 200 ] || http_fail "$code" "讀取 pipeline $id"
    pyjson "c=d.get('configuration') or {}; p=c.get('path') or ''; print(d.get('id',''), d.get('name',''), (c.get('repository') or {}).get('id',''), '/'+p.lstrip('/'), sep='\t')" >> "$PIPES_TSV"
  done
}
# pipe_for <repo id>：指向該 repo 的 /azure-pipelines.yml 的 pipeline id（逗號分隔）
pipe_for() { awk -F'\t' -v r="$1" 'tolower($3)==tolower(r) && tolower($4)=="/azure-pipelines.yml" {print $1}' "$PIPES_TSV" | paste -sd, -; }

MAIN_PIPES=""
SECURE_FILES=""
[ -n "$PROJ_ID" ] && load_pipes
for i in "${!N_NAME[@]}"; do
  [ "${N_ROLE[$i]}" = main ] || continue
  name="${N_ADO[$i]}"
  if [ -z "${N_ID[$i]}" ] || [ "${N_REFS[$i]:-0}" = 0 ]; then
    echo "  ${name}：repo 還沒有內容，無法確認 /azure-pipelines.yml，推送後再執行一次"
    continue
  fi
  q="path=%2Fazure-pipelines.yml&includeContent=true&%24format=json&api-version=$api"
  want="${N_BR[$i]%%,*}"
  [ -n "$want" ] && q="versionDescriptor.version=$(urlenc "$want")&versionDescriptor.versionType=branch&$q"
  code="$(req GET "$BASE/git/repositories/${N_ID[$i]}/items?$q")"
  case "$code" in
    200) ;;
    404) echo "  ${name}：沒有 /azure-pipelines.yml${want:+（分支 ${want}）}，不建 pipeline"; continue ;;
    *) http_fail "$code" "檢查 ${name} 的 azure-pipelines.yml" ;;
  esac
  pyjson "print(d.get('content') or '')" >| "$WORK/yaml-$i.yml"
  N_YAML[$i]="$WORK/yaml-$i.yml"
  sf="$(pyjson "import re; print(','.join(sorted(set(re.findall(r'secureFile:\s*[\"\x27]?([^\s\"\x27#]+)', d.get('content') or '')))))")"
  [ -n "$sf" ] && SECURE_FILES="${SECURE_FILES:+$SECURE_FILES,}$sf"
  existing="$(pipe_for "${N_ID[$i]}")"
  if [ -n "$existing" ]; then
    echo "  ${name}：已有指向 /azure-pipelines.yml 的 pipeline（id ${existing}），沿用"
    MAIN_PIPES="${MAIN_PIPES:+$MAIN_PIPES,}$existing"; N_PIPE[$i]="$existing"
    continue
  fi
  plan "建立 pipeline「${name} CI」（folder \\，yaml /azure-pipelines.yml），建完重新列出確認（踩坑 34）"
  if [ "$APPLY" -eq 1 ]; then
    mkbody "$WORK/pipe.json" '{"name": a[0] + " CI", "folder": "\\", "configuration": {"type": "yaml", "path": "/azure-pipelines.yml", "repository": {"id": a[1], "name": a[0], "type": "azureReposGit"}}}' "$name" "${N_ID[$i]}"
    pcode="$(req POST "$BASE/pipelines?api-version=$PIPE_API" "$WORK/pipe.json")"
    cp "$RESP" "$WORK/pipe-post-resp.json"
    load_pipes
    existing="$(pipe_for "${N_ID[$i]}")"
    via=""
    if [ -z "$existing" ] && [ "$pcode" = 500 ]; then
      # 踩坑 58：pipelines API 只到 repo 的預設分支找 yaml，yaml 只在別的分支時回 500「找不到檔案」。
      # 改用 build/definitions 並在 repository.defaultBranch 指定含 yaml 的分支，不必改 repo 的預設分支。
      echo "    建立回 HTTP 500（pipelines API 只到 repo 預設分支找 yaml），改用 build/definitions 指定分支建立（踩坑 58）"
      cp "$WORK/pipe-post-resp.json" "$RESP"
      head -c 300 "$RESP" | sed 's/^/    回應：/'; echo
      br="${want:+refs/heads/$want}"
      [ -n "$br" ] || br="$(repo_field "$name" defaultBranch)"
      [ -n "$br" ] || mismatch "不知道 ${name} 的 yaml 在哪個分支（.ci-tools.yml 的 branches 是空的，repo 也沒有預設分支）"
      code="$(req GET "$BASE/distributedtask/queues?queueName=$(urlenc "$QUEUE")&api-version=$api")"
      [ "$code" = 200 ] || http_fail "$code" "查詢 agent 佇列「${QUEUE}」"
      qid="$(pyjson "v=[q for q in d.get('value',[]) if (q.get('name') or '').lower()==sys.argv[2].lower()]; print(v[0]['id'] if v else '')" "$QUEUE")"
      [ -n "$qid" ] || mismatch "專案裡找不到 agent 佇列「${QUEUE}」（.ci-tools.yml 的 agent_queue）"
      mkbody "$WORK/def.json" '{"name": a[0] + " CI", "type": "build", "queue": {"id": int(a[2])}, "process": {"type": 2, "yamlFilename": "/azure-pipelines.yml"}, "repository": {"id": a[1], "name": a[0], "type": "TfsGit", "defaultBranch": a[3]}}' "$name" "${N_ID[$i]}" "$qid" "$br"
      pcode="$(req POST "$BASE/build/definitions?api-version=$api" "$WORK/def.json")"
      cp "$RESP" "$WORK/pipe-post-resp.json"
      load_pipes
      existing="$(pipe_for "${N_ID[$i]}")"
      via="（build/definitions，分支 ${br}）"
    fi
    if [ -z "$existing" ]; then
      case "$pcode" in
        200|201) mismatch "建立 pipeline${via}回 HTTP ${pcode}，但重新列出時找不到指向 ${name} /azure-pipelines.yml 的 pipeline" ;;
        *) cp "$WORK/pipe-post-resp.json" "$RESP"; http_fail "$pcode" "建立 pipeline（${name} CI）${via}，重新列出也沒有" ;;
      esac
    fi
    case "$pcode" in
      200|201) echo "    已建立${via}並讀回：pipeline id ${existing}" ;;
      *) echo "    建立${via}回 HTTP ${pcode}，但重新列出已有 pipeline id ${existing}，視為成功（踩坑 34）" ;;
    esac
    MAIN_PIPES="${MAIN_PIPES:+$MAIN_PIPES,}$existing"; N_PIPE[$i]="$existing"
  fi
done
echo

# ---------------------------------------------------------------- 6. 授權
echo "== 6. 授權"
REPO_ROOT_DIR="$(cd "$(dirname "$CONFIG")/.." && pwd)"
auth_args=(--config "$CONFIG" --servers "$SERVERS" --project "$PROJECT")
# ensure_auth <ado-authorize 的資源旗標> <名稱> <說明> <pipeline ids>：先 list（只發 GET）查已授權的 pipeline，只對缺的 grant
ensure_auth() {
  local flag="$1" res="$2" label="$3" ids="$4" out have missing="" id
  local want_ids=()
  out="$(bash "$AUTH" list "$flag" "$res" "${auth_args[@]}")" || exit $?
  have="${out##*已授權的 pipeline：}"
  IFS=',' read -r -a want_ids <<<"$ids"
  for id in "${want_ids[@]}"; do
    if [ "$have" = ALL ] || [[ ",$have," == *",$id,"* ]]; then continue; fi
    missing="${missing:+$missing,}$id"
  done
  if [ -z "$missing" ]; then
    echo "  ${label}：已授權 pipeline ${have}，沿用"
    return 0
  fi
  echo "  ${label}：目前已授權 ${have}"
  plan "授權 pipeline ${missing} 使用${label}"
  if [ "$APPLY" -eq 1 ]; then bash "$AUTH" grant "$flag" "$res" --pipelines "$missing" "${auth_args[@]}" | sed 's/^/    /'; fi
}
# yaml_refs_repo <yaml 檔> <repo 名稱>：yaml 的 resources.repositories 有 name: <repo> 或 name: <專案>/<repo> 時印 yes
yaml_refs_repo() {
  python3 - "$1" "$2" <<'PY2'
import re, sys
lines = open(sys.argv[1], encoding="utf-8-sig", errors="replace").read().splitlines()
want = sys.argv[2].lower()

def ind(l):
    return len(l) - len(l.lstrip(" "))

def body(l):
    return re.sub(r"\s+#.*$", "", l).strip()

names, i = [], 0
while i < len(lines):
    if body(lines[i]) == "resources:":
        r = ind(lines[i]); i += 1
        while i < len(lines) and (not body(lines[i]) or ind(lines[i]) > r):
            if body(lines[i]) == "repositories:":
                k = ind(lines[i]); i += 1
                while i < len(lines):
                    b = body(lines[i])
                    if b and (ind(lines[i]) < k or (ind(lines[i]) == k and not b.startswith("-"))):
                        break
                    m = re.match(r"^(?:-\s*)?name\s*:\s*(.+)$", b)
                    if m:
                        names.append(m.group(1).strip().strip("'\""))
                    i += 1
                continue
            i += 1
        continue
    i += 1
print("yes" if any(n.lower() == want or n.lower().endswith("/" + want) for n in names) else "no")
PY2
}
if [ -z "$MAIN_PIPES" ]; then
  echo "  還沒有 main repo 的 pipeline，略過授權（pipeline 建好後再執行一次）"
else
  ensure_auth --queue "$QUEUE" "agent 佇列「${QUEUE}」（踩坑 35）" "$MAIN_PIPES"
  for d in "${!N_NAME[@]}"; do
    [ "${N_ROLE[$d]}" = dependency ] || continue
    dep="${N_ADO[$d]}"; pids=""
    # 只授權 yaml 的 resources.repositories 實際引用這個依賴 repo 的 pipeline
    for i in "${!N_NAME[@]}"; do
      [ -n "${N_PIPE[$i]}" ] || continue
      yfile="${N_YAML[$i]}"; ysrc="ADO 預設分支"
      if [ -z "$yfile" ] || [ ! -s "$yfile" ]; then
        yfile="$REPO_ROOT_DIR/${N_NAME[$i]}/azure-pipelines.yml"; ysrc="本機"
      fi
      if [ ! -f "$yfile" ]; then
        echo "  依賴 repo「${dep}」：pipeline ${N_PIPE[$i]}（${N_ADO[$i]}）的 yaml 在 ADO 與本機都讀不到，不授權"
        continue
      fi
      if [ "$(yaml_refs_repo "$yfile" "$dep")" = yes ]; then
        echo "  依賴 repo「${dep}」：pipeline ${N_PIPE[$i]}（${N_ADO[$i]}）的 yaml（${ysrc}）在 resources.repositories 引用它"
        pids="${pids:+$pids,}${N_PIPE[$i]}"
      else
        echo "  依賴 repo「${dep}」：pipeline ${N_PIPE[$i]}（${N_ADO[$i]}）的 yaml（${ysrc}）沒有引用它，不授權"
      fi
    done
    if [ -n "$pids" ]; then
      ensure_auth --repo "$dep" "依賴 repo「${dep}」" "$pids"
    fi
  done
fi
echo

# ---------------------------------------------------------------- 7. 環境一致性（只讀）
echo "== 7. 環境一致性（只讀，不改檔）"
MAIN_NAMES=()
for i in "${!N_NAME[@]}"; do [ "${N_ROLE[$i]}" = main ] && MAIN_NAMES+=("${N_NAME[$i]}"); done
python3 - "$HERE/lib" "$SERVERS" "$SERVER_NAME" "$REPO_ROOT_DIR" ${MAIN_NAMES[@]+"${MAIN_NAMES[@]}"} >| "$WORK/env.txt" <<'PY'
import glob, os, re, sys
from urllib.parse import urlparse
sys.path.insert(0, sys.argv[1])
import ci_config as c
servers_path, server, root, names = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
servers = c._servers(servers_path)
cur = servers.get(server) or {}

def norm(p):
    return str(p).strip().strip("'\"").replace("/", "\\").rstrip("\\").lower()

# 正規化後的路徑 → servers.yml 裡的原文（比對不分大小寫、斜線方向，顯示用原文）
jdks, ghome, npm = {}, {}, {}
for a in cur.get("agents") or []:
    a = a or {}
    for v in (a.get("jdk") or {}).values():
        jdks[norm(v)] = str(v)
    if a.get("gradle_user_home"): ghome[norm(a["gradle_user_home"])] = str(a["gradle_user_home"])
    if a.get("npm_cache"): npm[norm(a["npm_cache"])] = str(a["npm_cache"])
expect = {"JAVA_HOME": jdks, "GRADLE_USER_HOME": ghome, "NPM_CONFIG_CACHE": npm}

def hosts(s):
    out = set()
    for k in ("url", "ssh_url"):
        v = (s or {}).get(k)
        if v:
            h = urlparse(str(v).replace("{", "").replace("}", "")).hostname
            if h: out.add(h.lower())
    return out
mine = hosts(cur)
others = {}
for n, s in servers.items():
    if n == server: continue
    for h in hosts(s) - mine:
        others[h] = n

KEYS = "JAVA_HOME|GRADLE_USER_HOME|NPM_CONFIG_CACHE"
# map 形式：  JAVA_HOME: 'C:\x'
pat_map = re.compile(r"^\s*(?:-\s*)?(" + KEYS + r")\s*:\s*(.+?)\s*(#.*)?$")
# 清單形式：  - name: JAVA_HOME   下一行（同一個清單項目內）  value: 'C:\x'
pat_name = re.compile(r"^(\s*)-\s*name\s*:\s*[\"']?(" + KEYS + r")[\"']?\s*(#.*)?$")
pat_value = re.compile(r"^\s*value\s*:\s*(.+?)\s*(#.*)?$")

def indent(line):
    return len(line) - len(line.lstrip(" "))

def find_vars(lines):
    """回傳 [(行號, key, 值)]：值已去掉引號"""
    out = []
    for i, line in enumerate(lines):
        m = pat_map.match(line)
        if m:
            out.append((i + 1, m.group(1), m.group(2).strip().strip("'\"")))
            continue
        m = pat_name.match(line)
        if not m:
            continue
        dash = len(m.group(1))
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            if indent(nxt) <= dash:      # 下一個清單項目或上一層：這個項目沒有 value
                break
            v = pat_value.match(nxt)
            if v:
                out.append((j + 1, m.group(2), v.group(1).strip().strip("'\"")))
                break
    return out
for name in names:
    d = os.path.join(root, name)
    y = os.path.join(d, "azure-pipelines.yml")
    if not os.path.isfile(y):
        print(f"INFO\t{name}/azure-pipelines.yml：找不到（{y}），略過")
        continue
    lines = open(y, encoding="utf-8-sig", errors="replace").read().splitlines()
    found = {}
    for no, key, val in find_vars(lines):
        found.setdefault(key, []).append((no, val))
    summary = []
    for key in ("JAVA_HOME", "GRADLE_USER_HOME", "NPM_CONFIG_CACHE"):
        if key not in found:
            summary.append(f"{key} 未設定")
            continue
        for no, val in found[key]:
            if "$(" in val or "${{" in val or "$[" in val:
                summary.append(f"{key}＝{val}（由變數或參數決定，不比對）")
            elif not expect[key]:
                summary.append(f"{key}＝{val}（servers.yml 沒有對應路徑，無法比對）")
                print(f"WARN\t{name}/azure-pipelines.yml:{no} {key}={val}，但 servers.yml 的 {server} agents 沒有對應路徑可比對")
            elif norm(val) in expect[key]:
                summary.append(f"{key}＝{val}（與 agent 一致）")
            else:
                summary.append(f"{key}＝{val}（與 agent 不一致）")
                print(f"WARN\t{name}/azure-pipelines.yml:{no} {key}={val} 與 servers.yml 的 {server} agents 不一致（agents：{'、'.join(sorted(expect[key].values()))}）")
    print(f"INFO\t{name}/azure-pipelines.yml：" + "、".join(summary))
    files = [y] + sorted(glob.glob(os.path.join(d, "bin", "*.sh")))
    for f in files:
        for no, line in enumerate(open(f, encoding="utf-8-sig", errors="replace").read().splitlines(), 1):
            low = line.lower()
            for h, n in sorted(others.items()):
                if h in low:
                    print(f"WARN\t{os.path.relpath(f, root)}:{no} 含其他 server（{n}）的主機 {h}，搬到 {server} 時要改")
PY
while IFS=$'\t' read -r kind msg; do
  [ -n "$kind" ] || continue
  if [ "$kind" = WARN ]; then warn "$msg"; else echo "  $msg"; fi
done < "$WORK/env.txt"

# ---------------------------------------------------------------- 結尾提示
echo
echo "== 結尾"
if [ -n "$SECURE_FILES" ]; then
  echo "  yaml 引用了 secure file：$(printf '%s' "$SECURE_FILES" | tr ',' '\n' | sort -u | paste -sd, -)"
  echo "  本腳本不處理 secure file：第一次建置前要先上傳到 Library → Secure files，再授權 pipeline"
  echo "  （ado-authorize.sh grant --securefile <名稱> --pipelines <id>），否則整條 pipeline 會在驗證階段失敗（踩坑 45）"
fi
if [ "$APPLY" -eq 0 ]; then
  echo "  （預覽模式，未寫入任何東西。確認無誤後加 --apply。）"
else
  echo "  完成。下一步：第一次建置用 ado-run.sh --pipeline <id> -p offline=false（踩坑 25、38）"
fi
[ "$WARN" -eq 0 ] || echo "  共 ${WARN} 則警告（見上方）"
exit 0
