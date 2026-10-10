#!/usr/bin/env bash
# ado-authorize.sh — 查詢、授權或撤銷 pipeline 對受保護資源（agent 佇列、repo、secure file）的使用權
#
# 為什麼需要：新專案的 pipeline 第一次使用 agent 佇列、引用別的 repo、或下載 secure file 時，
# 都要先授權，否則建置一直停在 notStarted、agent 卻閒置（見 pitfalls.md 第 35 條「新專案的 pipeline 一直停在 notStarted，agent 卻閒著」）。網頁上是執行頁的「Permit」按鈕。
# 另一個用途：建立或移動「會觸發部署」的分支前，先撤銷 pipeline 對部署金鑰（secure file）的授權，
# 讓自動觸發的那次卡住、取消後再恢復（cd-ssh-vm.md）。
#
# grant／revoke 會寫入 ADO，寫完一定讀回比對，不一致就以非 0 結束。list 只發 GET。
# 權杖只從環境變數讀（servers.yml 的 pat_env），以 curl 設定檔從 stdin 傳入，不出現在指令列，也不會印出。
#
# 用法：
#   ado-authorize.sh list   <資源> [選項]
#   ado-authorize.sh grant  <資源> --pipelines <id,id,...> [選項]
#   ado-authorize.sh revoke <資源> --pipelines <id,id,...> [選項]
#
# 資源（擇一）：
#   --queue <名稱>        agent 佇列（例：Default）
#   --repo <名稱>         本專案內的 repo（多 repo 並排 checkout 時，被引用的那個）
#   --securefile <名稱>   Library 裡的 secure file（例：部署用 SSH 私鑰）
#
# 選項：
#   --pipelines <ids>     pipeline id，逗號分隔（grant／revoke 必填）
#   --config <path>       專案層 .ci-tools.yml（預設：目前目錄的 .ci-tools.yml）
#   --servers <path>      使用者層 servers.yml（預設：${CI_TOOLS_SERVERS}，
#                         否則 ${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml）
#   --project <name>      ADO 專案名稱（預設：.ci-tools.yml 的 ado_project）
#   -h, --help            顯示說明
#
# 結束碼：0 完成；1 參數、設定或連線錯誤；2 寫入後讀回與預期不符

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/lib/ci_config.py"

ACTION="${1:-}"
case "$ACTION" in
  list|grant|revoke) shift ;;
  -h|--help) usage; exit 0 ;;
  *) echo "錯誤：第一個參數要是 list、grant 或 revoke" >&2; usage >&2; exit 1 ;;
esac

KIND=""; RES_NAME=""; PIPES=""
CONFIG=""
SERVERS="${CI_TOOLS_SERVERS:-${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml}"
PROJECT=""
set_res() {
  [ -z "$KIND" ] || { echo "錯誤：--queue、--repo、--securefile 只能擇一" >&2; exit 1; }
  KIND="$1"; RES_NAME="$2"
}
while [ $# -gt 0 ]; do
  case "$1" in
    --queue) set_res queue "${2:-}"; shift 2 ;;
    --repo) set_res repository "${2:-}"; shift 2 ;;
    --securefile) set_res securefile "${2:-}"; shift 2 ;;
    --pipelines) PIPES="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --servers) SERVERS="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "錯誤：需要 python3" >&2; exit 1; }
[ -n "$KIND" ] && [ -n "$RES_NAME" ] || { echo "錯誤：要指定 --queue、--repo 或 --securefile 其中之一" >&2; exit 1; }
if [ "$ACTION" != list ]; then
  [[ "$PIPES" =~ ^[0-9]+(,[0-9]+)*$ ]] || { echo "錯誤：--pipelines 要是逗號分隔的數字 id（例：3,4）" >&2; exit 1; }
fi
if [ -z "$CONFIG" ] && [ -f "$PWD/.ci-tools.yml" ]; then CONFIG="$PWD/.ci-tools.yml"; fi
[ -n "$CONFIG" ] || { echo "錯誤：需要 --config（或目前目錄有 .ci-tools.yml）" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "錯誤：找不到 ${CONFIG}" >&2; exit 1; }
[ -f "$SERVERS" ] || { echo "錯誤：找不到 servers.yml：${SERVERS}" >&2; exit 1; }

[ -n "$PROJECT" ] || PROJECT="$(python3 "$CFG" get "$CONFIG" ado_project 2>/dev/null || true)"
[ -n "$PROJECT" ] || { echo "錯誤：不知道 ADO 專案名稱，請在 .ci-tools.yml 寫 ado_project 或給 --project" >&2; exit 1; }
row="$(python3 "$CFG" project-server "$CONFIG" "$SERVERS")" || { echo "錯誤：.ci-tools.yml 沒有可用的 server" >&2; exit 1; }
IFS=$'\x1f' read -r _name url coll pat_env _key _ssh api <<<"$row"
[ -n "${!pat_env:-}" ] || { echo "錯誤：環境變數 $pat_env 未設定（servers.yml 的 pat_env）" >&2; exit 1; }
[ -n "$api" ] || api="6.0"
ROOT="${url%/}/$coll"
BASE="$ROOT/$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$PROJECT")/_apis"
PERM_API="6.1-preview.1"     # pipelinePermissions 在 Server 版要用這個版本
SF_API="6.0-preview.1"

WORK="$(mktemp -d)"
RESP="$WORK/resp.json"

req() {
  local method="$1" u="$2" body="${3:-}" code
  local args=(-sS -m 60 -K - -o "$RESP" -w '%{http_code}' -X "$method")
  if [ -n "$body" ]; then args+=(-H 'Content-Type: application/json' --data-binary "@$body"); fi
  code="$(printf 'user = ":%s"\n' "${!pat_env}" | curl "${args[@]}" "$u" 2>/dev/null)" || code="000"
  printf '%s' "$code"
}
http_fail() {
  echo "錯誤：$2 失敗（HTTP $1）" >&2
  case "$1" in
    401) echo "  401：查詢也失敗多半是 IIS 基本驗證（踩坑 5）；只有寫入失敗多半是權杖範圍不足（踩坑 32）" >&2 ;;
    404) echo "  404：資源不存在，或權杖看不到它" >&2 ;;
    000) echo "  連不到 ADO（網址或網路）" >&2 ;;
  esac
  [ -s "$RESP" ] && head -c 400 "$RESP" | sed 's/^/  回應：/' >&2 && echo >&2
  exit 1
}
urlenc() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
pyjson() { python3 -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); $1" "$RESP" "${@:2}"; }

# ---------------------------------------------------------------- 1. 資源名稱 → pipelinePermissions 的資源 id
case "$KIND" in
  queue)
    code="$(req GET "$BASE/distributedtask/queues?queueName=$(urlenc "$RES_NAME")&api-version=$api")"
    [ "$code" = 200 ] || http_fail "$code" "查詢 agent 佇列"
    RID="$(pyjson "v=d.get('value',[]); print(v[0]['id'] if v else '')")" ;;
  repository)
    code="$(req GET "$ROOT/_apis/projects/$(urlenc "$PROJECT")?api-version=$api")"
    [ "$code" = 200 ] || http_fail "$code" "查詢專案"
    proj_id="$(pyjson "print(d.get('id',''))")"
    code="$(req GET "$BASE/git/repositories/$(urlenc "$RES_NAME")?api-version=$api")"
    [ "$code" = 200 ] || http_fail "$code" "查詢 repo"
    repo_id="$(pyjson "print(d.get('id',''))")"
    # repository 型資源的 id 是「專案 id.repo id」
    RID="${proj_id:+$proj_id.$repo_id}" ;;
  securefile)
    code="$(req GET "$BASE/distributedtask/securefiles?api-version=$SF_API")"
    [ "$code" = 200 ] || http_fail "$code" "查詢 secure files"
    RID="$(pyjson "v=[f['id'] for f in d.get('value',[]) if f.get('name')==sys.argv[2]]; print(v[0] if v else '')" "$RES_NAME")" ;;
esac
[ -n "$RID" ] || { echo "錯誤：找不到 ${KIND}「${RES_NAME}」" >&2; exit 1; }
PERM_URL="$BASE/pipelines/pipelinePermissions/$KIND/$RID?api-version=$PERM_API"

# authorized_ids：印出目前已授權的 pipeline id（逗號分隔，排序）；全部 pipeline 都授權時印 ALL
authorized_ids() {
  local code
  code="$(req GET "$PERM_URL")"
  [ "$code" = 200 ] || http_fail "$code" "查詢授權狀態"
  pyjson "
if (d.get('allPipelines') or {}).get('authorized'): print('ALL')
else: print(','.join(str(i) for i in sorted(p['id'] for p in d.get('pipelines',[]) if p.get('authorized'))))"
}

if [ "$ACTION" = list ]; then
  ids="$(authorized_ids)"
  echo "${KIND}「${RES_NAME}」（${RID}）已授權的 pipeline：${ids:-（無）}"
  exit 0
fi

# ---------------------------------------------------------------- 2. 寫入並讀回
flag=true; [ "$ACTION" = revoke ] && flag=false
python3 - "$WORK/body.json" "$PIPES" "$flag" <<'PY'
import json, sys
out, ids, flag = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
json.dump({"pipelines": [{"id": int(i), "authorized": flag} for i in ids.split(",")]}, open(out, "w"))
PY
code="$(req PATCH "$PERM_URL" "$WORK/body.json")"
[ "$code" = 200 ] || http_fail "$code" "$ACTION pipeline $PIPES"

now="$(authorized_ids)"
bad=""
IFS=',' read -r -a want <<<"$PIPES"
for i in "${want[@]}"; do
  if [ "$now" = ALL ]; then has=1; elif [[ ",$now," == *",$i,"* ]]; then has=1; else has=0; fi
  if [ "$ACTION" = grant ] && [ "$has" = 0 ]; then bad="$bad $i"; fi
  if [ "$ACTION" = revoke ] && [ "$has" = 1 ]; then bad="$bad $i"; fi
done
echo "${KIND}「${RES_NAME}」讀回已授權的 pipeline：${now:-（無）}"
if [ -n "$bad" ]; then
  echo "錯誤：$ACTION 後讀回與預期不符（pipeline${bad}）" >&2
  [ "$now" = ALL ] && echo "  這個資源設為「所有 pipeline 都可使用」，個別撤銷無效，需先在網頁關掉該設定" >&2
  exit 2
fi
echo "$ACTION 完成並已讀回確認：pipeline $PIPES"
