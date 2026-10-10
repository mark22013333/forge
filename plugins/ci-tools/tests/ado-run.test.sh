#!/usr/bin/env bash
# ado-run.sh 的離線測試：PATH 前放一支假 curl，依 URL 回固定 JSON，並記錄每個請求，不連任何網路。
# 執行：bash plugins/ci-tools/tests/ado-run.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="$HERE/../scripts/ado-run.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ---------------------------------------------------------------- 假 curl
# 路由檔每行：方法<TAB>URL 片段<TAB>HTTP 碼<TAB>回應檔（多個以逗號分隔，依呼叫次數輪流、停在最後一個）
# 每次請求記錄到 ${FAKE_LOG}：方法<TAB>URL<TAB>body；stdin（權杖設定）另存 ${FAKE_STDIN}；argv 存 $FAKE_ARGV
mkdir -p "$T/bin"
cat >| "$T/bin/curl" <<'EOF'
#!/usr/bin/env bash
method=GET; out=""; body=""; url=""
printf '%s\n' "$*" >> "$FAKE_ARGV"
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    -w|-m|-H) shift 2 ;;
    -K) cat >> "$FAKE_STDIN"; shift 2 ;;
    --data-binary) body="$(cat "${2#@}")"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
printf '%s\t%s\t%s\n' "$method" "$url" "$body" >> "$FAKE_LOG"
n=0
while IFS=$'\t' read -r m frag code files; do
  n=$((n + 1))
  [ "$m" = "$method" ] || continue
  case "$url" in *"$frag"*) ;; *) continue ;; esac
  c=$(cat "$FAKE_STATE/$n" 2>/dev/null || echo 0); echo $((c + 1)) >| "$FAKE_STATE/$n"
  IFS=',' read -r -a arr <<<"$files"
  i=$c; [ "$i" -ge "${#arr[@]}" ] && i=$(( ${#arr[@]} - 1 ))
  cat "$FAKE_DIR/${arr[$i]}" >| "$out"
  printf '%s' "$code"
  exit 0
done < "$FAKE_ROUTES"
: >| "$out"; printf '404'
EOF
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH" FAKE_DIR="$T/r"
mkdir -p "$T/r"

# 回應檔
cat >| "$T/r/pipelines.json" <<'EOF'
{"count":2,"value":[{"id":3,"name":"web CI"},{"id":7,"name":"app CI"}]}
EOF
echo '{"id":101,"name":"20260101.1","state":"inProgress"}' >| "$T/r/run.json"
echo '{"id":101,"status":"inProgress"}' >| "$T/r/b-progress.json"
echo '{"id":101,"status":"completed","result":"succeeded"}' >| "$T/r/b-ok.json"
echo '{"id":101,"status":"completed","result":"failed"}' >| "$T/r/b-fail.json"
echo '{"id":101,"status":"completed","result":"canceled"}' >| "$T/r/b-cancel.json"
cat >| "$T/r/timeline.json" <<'EOF'
{"records":[{"type":"Stage","name":"Build","result":"failed"},
 {"type":"Task","name":"初始化","result":"succeeded","log":{"id":2}},
 {"type":"Task","name":"Gradle 建置","result":"failed","log":{"id":5}}]}
EOF
printf 'line1\nline2\nERROR-LINE-XYZ: compile failed\n' >| "$T/r/log5.txt"
echo '{"message":"unauthorized"}' >| "$T/r/err.json"

# 設定檔（example 值）
cat >| "$T/servers.yml" <<'EOF'
default_server: ex-ado
servers:
  ex-ado:
    url: http://ado.example:8080
    collection: Coll
    api_version: "6.0"
    pat_env: EX_PAT
EOF
cat >| "$T/.ci-tools.yml" <<'EOF'
server: ex-ado
ado_project: proj-x
EOF
export EX_PAT="secret-token-123"

# scenario <名稱> <路由行...>：重設紀錄與計數
scenario() {
  echo "== $1"; shift
  S="$T/s$((++SN))"; mkdir -p "$S/state"
  export FAKE_LOG="$S/log" FAKE_STDIN="$S/stdin" FAKE_ARGV="$S/argv" FAKE_STATE="$S/state" FAKE_ROUTES="$S/routes"
  : >| "$FAKE_LOG"; : >| "$FAKE_STDIN"; : >| "$FAKE_ARGV"
  printf '%s\n' "$@" >| "$FAKE_ROUTES"
}
SN=0
R_PIPES=$'GET\t/_apis/pipelines?\t200\tpipelines.json'
R_RUN=$'POST\t/_apis/pipelines/7/runs\t200\trun.json'
R_TL=$'GET\t/builds/101/timeline\t200\ttimeline.json'
R_LOG=$'GET\t/builds/101/logs/5\t200\tlog5.txt'
args=(--config "$T/.ci-tools.yml" --servers "$T/servers.yml" --poll 0)

scenario "情境 1：名稱轉 id、分支與參數、輪詢到成功" "$R_PIPES" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-progress.json,b-progress.json,b-ok.json'
OUT="$(bash "$RUN" "${args[@]}" --pipeline "app CI" --branch main -p offline=false -p deployDryRun=true 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "名稱「app CI」轉成 id 7" 'echo "$OUT" | grep -q "→ id 7"'
check "POST 到 proj-x 的 pipelines/7/runs（preview 版本）" 'grep -q "^POST	http://ado.example:8080/Coll/proj-x/_apis/pipelines/7/runs?api-version=6.0-preview.1	" "$FAKE_LOG"'
BODY="$(grep "^POST" "$FAKE_LOG" | cut -f3)"
check "body 帶 refs/heads/main" 'echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"resources\"][\"repositories\"][\"self\"][\"refName\"]==\"refs/heads/main\""'
check "templateParameters 兩個值都是字串" 'echo "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"templateParameters\"]=={\"offline\":\"false\",\"deployDryRun\":\"true\"}"'
check "輪詢 3 次才完成" '[ "$(grep -c "/build/builds/101?" "$FAKE_LOG")" = 3 ]'
check "權杖經 stdin 傳入" 'grep -q "user = \":secret-token-123\"" "$FAKE_STDIN"'
check "權杖不在指令列參數" '! grep -q secret-token-123 "$FAKE_ARGV"'
check "權杖沒有印出" '! echo "$OUT" | grep -q secret-token-123'

scenario "情境 2：失敗時印出失敗步驟與 log 末段" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-fail.json' "$R_TL" "$R_LOG"
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 2" '[ $RC -eq 2 ]'
check "數字 id 不查 pipeline 清單" '! grep -q "/_apis/pipelines?" "$FAKE_LOG"'
check "沒給分支就不帶 resources" '! grep "^POST" "$FAKE_LOG" | cut -f3 | grep -q resources'
check "印出失敗步驟名稱" 'echo "$OUT" | grep -q "失敗步驟：Gradle 建置（log 5）"'
check "印出 log 內容" 'echo "$OUT" | grep -q "ERROR-LINE-XYZ"'
check "成功的步驟不抓 log" '! grep -q "/logs/2" "$FAKE_LOG"'

scenario "情境 3：觸發回 401 → 說明權杖範圍" $'POST\t/_apis/pipelines/7/runs\t401\terr.json'
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 2>&1)"; RC=$?
check "exit 1" '[ $RC -eq 1 ]'
check "提到 HTTP 401 與 Build: Read & execute" 'echo "$OUT" | grep -q "HTTP 401" && echo "$OUT" | grep -q "Build: Read & execute"'

scenario "情境 4：--no-wait 只觸發" "$R_RUN"
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 --no-wait 2>&1)"; RC=$?
check "exit 0 且印出 run id" '[ $RC -eq 0 ] && echo "$OUT" | grep -q "run 101"'
check "沒有查詢建置狀態" '! grep -q "/build/builds/" "$FAKE_LOG"'

scenario "情境 5：建置被取消" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-cancel.json'
bash "$RUN" "${args[@]}" --pipeline 7 >/dev/null 2>&1; RC=$?
check "exit 3" '[ $RC -eq 3 ]'

scenario "情境 6：等待逾時不取消建置" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-progress.json'
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 --timeout 0 2>&1)"; RC=$?
check "exit 4 並說明未取消" '[ $RC -eq 4 ] && echo "$OUT" | grep -q "未取消"'
check "沒有送出取消請求" '! grep -qE "^(PATCH|DELETE)" "$FAKE_LOG"'

scenario "情境 7：名稱找不到" "$R_PIPES"
OUT="$(bash "$RUN" "${args[@]}" --pipeline "nope" 2>&1)"; RC=$?
check "exit 1 並列出現有 pipeline" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "app CI"'
check "沒有觸發" '! grep -q "^POST" "$FAKE_LOG"'

scenario "情境 8：參數錯誤不發請求"
bash "$RUN" "${args[@]}" --pipeline 7 -p noequals >/dev/null 2>&1; RC=$?
check "-p 沒有 = → exit 1" '[ $RC -eq 1 ]'
bash "$RUN" "${args[@]}" >/dev/null 2>&1; RC=$?
check "沒有 --pipeline → exit 1" '[ $RC -eq 1 ]'
check "以上都沒有發出請求" '[ ! -s "$FAKE_LOG" ]'
OUT="$(cd "$T" && EX_PAT="" bash "$RUN" --servers "$T/servers.yml" --pipeline 7 2>&1)"; RC=$?
check "權杖環境變數未設定 → exit 1" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "EX_PAT 未設定"'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
