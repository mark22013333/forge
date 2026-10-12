#!/usr/bin/env bash
# ado-run.sh — 以 REST API 觸發 ADO pipeline（可帶 templateParameters），輪詢到完成，失敗時印出驗證訊息與失敗步驟的 log
#
# 會對 ADO 送出一個寫入動作：排入一次執行（POST pipelines/<id>/runs）。其餘只發 GET。
# 權杖只從環境變數讀（servers.yml 的 pat_env），以 curl 設定檔從 stdin 傳入，不出現在指令列，也不會印出。
#
# 用法：
#   ado-run.sh --pipeline <名稱或 id> [選項]
#
# 選項：
#   --pipeline <名稱|id>  要執行的 pipeline（必填；名稱需完全相符）
#   --branch <分支>       執行的分支（例：main 或 refs/heads/main；省略時用 pipeline 的預設分支）
#   -p key=value          YAML 的 parameters 值（可重複；例：-p offline=false -p deployDryRun=true）
#   --no-wait             只觸發，不等結果
#   --poll <秒>           輪詢間隔（預設 30）
#   --timeout <分>        最多等幾分鐘（預設 180）；逾時不會取消建置，只是停止等待
#   --config <path>       專案層 .ci-tools.yml（預設：目前目錄的 .ci-tools.yml）
#   --servers <path>      使用者層 servers.yml（預設：${CI_TOOLS_SERVERS}，
#                         否則 ${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml）
#   --project <name>      ADO 專案名稱（預設：.ci-tools.yml 的 ado_project）
#   -h, --help            顯示說明
#
# 常見用途：
#   首次離線建置缺依賴 → ado-run.sh --pipeline "<名稱>" -p offline=false（踩坑 25、38）
#
# 結束碼：0 成功；1 參數、設定或連線錯誤；2 建置失敗；3 建置被取消；4 等待逾時

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/lib/ci_config.py"

PIPELINE=""
BRANCH=""
PARAMS=()
WAIT=1
POLL=30
TIMEOUT_MIN=180
CONFIG=""
SERVERS="${CI_TOOLS_SERVERS:-${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml}"
PROJECT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --pipeline) PIPELINE="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    -p) PARAMS+=("${2:-}"); shift 2 ;;
    --no-wait) WAIT=0; shift ;;
    --poll) POLL="${2:-}"; shift 2 ;;
    --timeout) TIMEOUT_MIN="${2:-}"; shift 2 ;;
    --config) CONFIG="${2:-}"; shift 2 ;;
    --servers) SERVERS="${2:-}"; shift 2 ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

command -v python3 >/dev/null 2>&1 || { echo "錯誤：需要 python3" >&2; exit 1; }
[ -n "$PIPELINE" ] || { echo "錯誤：--pipeline 必填" >&2; exit 1; }
for kv in ${PARAMS[@]+"${PARAMS[@]}"}; do
  [[ "$kv" == *=* ]] || { echo "錯誤：-p 要寫成 key=value：$kv" >&2; exit 1; }
done
[[ "$POLL" =~ ^[0-9]+$ ]] || { echo "錯誤：--poll 要是整數秒" >&2; exit 1; }
[[ "$TIMEOUT_MIN" =~ ^[0-9]+$ ]] || { echo "錯誤：--timeout 要是整數分鐘" >&2; exit 1; }
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
BASE="${url%/}/$coll/$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1]))' "$PROJECT")/_apis"
PIPE_API="6.0-preview.1"     # pipelines 與 runs 端點在 Server 版要用 preview 版本

WORK="$(mktemp -d)"
RESP="$WORK/resp.json"

# req <METHOD> <URL> [body 檔]：回應寫入 ${RESP}，印出 HTTP 狀態碼（連線失敗印 000）
req() {
  local method="$1" u="$2" body="${3:-}" code
  local args=(-sS -m 60 -K - -o "$RESP" -w '%{http_code}' -X "$method")
  if [ -n "$body" ]; then args+=(-H 'Content-Type: application/json' --data-binary "@$body"); fi
  code="$(printf 'user = ":%s"\n' "${!pat_env}" | curl "${args[@]}" "$u" 2>/dev/null)" || code="000"
  printf '%s' "$code"
}

# http_fail <狀態碼> <動作說明>：印出錯誤與常見原因後結束
http_fail() {
  echo "錯誤：$2 失敗（HTTP $1）" >&2
  case "$1" in
    401) echo "  401：查詢也失敗多半是 IIS 基本驗證（踩坑 5）；只有寫入失敗多半是權杖範圍不足——觸發建置需 Build: Read & execute（踩坑 32）" >&2 ;;
    404) echo "  404：專案、pipeline 或建置不存在，或權杖看不到它" >&2 ;;
    000) echo "  連不到 ADO（網址或網路）" >&2 ;;
  esac
  [ -s "$RESP" ] && head -c 400 "$RESP" | sed 's/^/  回應：/' >&2 && echo >&2
  exit 1
}

json() { python3 -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); $1" "$RESP"; }

# ---------------------------------------------------------------- 1. pipeline 名稱 → id
if [[ "$PIPELINE" =~ ^[0-9]+$ ]]; then
  PID="$PIPELINE"
else
  code="$(req GET "$BASE/pipelines?api-version=$PIPE_API")"
  [ "$code" = 200 ] || http_fail "$code" "查詢 pipeline 清單"
  PID="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); v=[p["id"] for p in d.get("value",[]) if p.get("name")==sys.argv[2]]; print(v[0] if v else "")' "$RESP" "$PIPELINE")"
  if [ -z "$PID" ]; then
    echo "錯誤：找不到名為「${PIPELINE}」的 pipeline。現有：" >&2
    json "[print('  ', p['id'], p.get('name')) for p in d.get('value',[])]" >&2
    exit 1
  fi
  echo "pipeline「${PIPELINE}」→ id $PID"
fi

# ---------------------------------------------------------------- 2. 觸發
python3 - "$WORK/run.json" "$BRANCH" ${PARAMS[@]+"${PARAMS[@]}"} <<'PY'
import json, sys
out, branch, kvs = sys.argv[1], sys.argv[2], sys.argv[3:]
body = {}
if branch:
    ref = branch if branch.startswith("refs/") else "refs/heads/" + branch
    body["resources"] = {"repositories": {"self": {"refName": ref}}}
if kvs:
    body["templateParameters"] = dict(kv.split("=", 1) for kv in kvs)
json.dump(body, open(out, "w", encoding="utf-8"), ensure_ascii=False)
PY
code="$(req POST "$BASE/pipelines/$PID/runs?api-version=$PIPE_API" "$WORK/run.json")"
[ "$code" = 200 ] || http_fail "$code" "觸發 pipeline $PID"
RUN_ID="$(json "print(d.get('id',''))")"
RUN_NAME="$(json "print(d.get('name',''))")"
[ -n "$RUN_ID" ] || { echo "錯誤：觸發後的回應沒有執行 id" >&2; exit 1; }
echo "已觸發：run ${RUN_ID}（${RUN_NAME}）參數：$(cat "$WORK/run.json")"

if [ "$WAIT" -eq 0 ]; then exit 0; fi

# ---------------------------------------------------------------- 3. 輪詢
deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 ))
last=""
while :; do
  code="$(req GET "$BASE/build/builds/$RUN_ID?api-version=$api")"
  [ "$code" = 200 ] || http_fail "$code" "查詢建置 $RUN_ID"
  state="$(json "print(d.get('status','') + '/' + str(d.get('result') or ''))")"
  if [ "$state" != "$last" ]; then echo "$(date +%T) 狀態：$state"; last="$state"; fi
  case "$state" in completed/*) break ;; esac
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "等待逾時（${TIMEOUT_MIN} 分鐘），建置仍在執行，未取消：run $RUN_ID" >&2
    exit 4
  fi
  sleep "$POLL"
done
result="${state#completed/}"

# ---------------------------------------------------------------- 4. 結果
# 驗證階段的錯誤（例：YAML 引用的 secure file 不存在或未授權，踩坑 45）只記在 build 回應的 validationResults，
# 此時建置沒有派工、timeline 是空的。${RESP} 此刻仍是最後一次 GET build 的回應。
VALIDATION_ERRORS=0
if [ "$result" != succeeded ]; then
  json "
for v in (d.get('validationResults') or []):
    if v.get('result')=='error':
        print('  [驗證錯誤] ' + (v.get('message') or ''))
" >| "$WORK/validation.txt"
  if [ -s "$WORK/validation.txt" ]; then
    VALIDATION_ERRORS=1
    echo "建置的驗證訊息（validationResults）："
    cat "$WORK/validation.txt"
  fi
fi
case "$result" in
  succeeded) echo "結果：成功（run ${RUN_ID}）"; exit 0 ;;
  canceled)  echo "結果：已取消（run ${RUN_ID}）"; exit 3 ;;
esac
echo "結果：${result}（run ${RUN_ID}）"
code="$(req GET "$BASE/build/builds/$RUN_ID/timeline?api-version=$api")"
if [ "$code" = 204 ]; then
  # 204 不是連線錯誤：建置在派工前就失敗，沒有任何 timeline record（踩坑 45）
  if [ "$VALIDATION_ERRORS" -eq 1 ]; then
    echo "timeline 為空：建置在派工前就失敗，見上方驗證訊息"
  else
    echo "timeline 為空：建置在派工前就失敗，但 build 回應沒有 error 等級的驗證訊息；請到網頁查看這次執行（run ${RUN_ID}）"
  fi
  exit 2
fi
[ "$code" = 200 ] || http_fail "$code" "取得 timeline"
# stage／job 層級的錯誤訊息（例：job 逾時「執行超過 60 分鐘」只記在 job 的 issues，步驟會是 canceled 而非 failed）
json "
for r in d.get('records',[]):
    if r.get('type') in ('Stage','Phase','Job') and r.get('result') in ('failed','canceled'):
        for i in (r.get('issues') or []):
            if i.get('type')=='error':
                print('  [' + r.get('type','') + ' ' + r.get('result','') + '] ' + r.get('name','') + '：' + (i.get('message') or ''))
"
json "
for r in d.get('records',[]):
    if r.get('type')=='Task' and r.get('result') in ('failed','canceled'):
        print(r.get('name',''), (r.get('log') or {}).get('id',''), r.get('result',''), sep='\t')
" >| "$WORK/failed.tsv"
if [ ! -s "$WORK/failed.tsv" ]; then
  echo "timeline 沒有失敗或被取消的步驟（可能失敗在 stage／job 層級，例如權限或資源未授權，見上方訊息）"
fi
while IFS=$'\t' read -r name logid tres; do
  echo "---- ${tres} 的步驟：${name}（log ${logid}）最後 40 行 ----"
  if [ -n "$logid" ]; then
    code="$(req GET "$BASE/build/builds/$RUN_ID/logs/$logid?api-version=$api")"
    if [ "$code" = 200 ]; then tail -40 "$RESP"; else echo "（取得 log 失敗：HTTP ${code}）"; fi
  fi
done < "$WORK/failed.tsv"
exit 2
