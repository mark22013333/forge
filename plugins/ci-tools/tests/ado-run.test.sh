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
# job 逾時：job 為 canceled 並帶 error issue，步驟為 canceled（不是 failed）
cat >| "$T/r/timeline-timeout.json" <<'EOF'
{"records":[{"type":"Stage","name":"Deploy","result":"failed"},
 {"type":"Job","name":"deploy","result":"canceled","issues":[{"type":"error","message":"JOB-TIMEOUT-MSG ran longer than 60 minutes"}]},
 {"type":"Task","name":"upload","result":"canceled","log":{"id":9}}]}
EOF
printf 'uploaded 140/210\nUPLOAD-TAIL-LINE\n' >| "$T/r/log9.txt"
echo '{"message":"unauthorized"}' >| "$T/r/err.json"
# 驗證階段失敗：build 帶 validationResults（error 與 warning 各一），timeline 回 204 空內容
cat >| "$T/r/b-invalid.json" <<'EOF'
{"id":101,"status":"completed","result":"failed","validationResults":[
 {"result":"error","message":"VALIDATION-ERR-MSG secureFile deploy-key"},
 {"result":"warning","message":"VALIDATION-WARN-MSG"}]}
EOF
echo '{"id":101,"status":"completed","result":"succeeded","validationResults":[{"result":"error","message":"SHOULD-NOT-PRINT"}]}' >| "$T/r/b-ok-warn.json"
: >| "$T/r/empty.txt"

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
check "印出失敗步驟名稱" 'echo "$OUT" | grep -q "failed 的步驟：Gradle 建置（log 5）"'
check "印出 log 內容" 'echo "$OUT" | grep -q "ERROR-LINE-XYZ"'
check "成功的步驟不抓 log" '! grep -q "/logs/2" "$FAKE_LOG"'

scenario "情境 2b：job 逾時 → 印出 job 的錯誤訊息與被取消步驟的 log" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-fail.json' $'GET\t/builds/101/timeline\t200\ttimeline-timeout.json' $'GET\t/builds/101/logs/9\t200\tlog9.txt'
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 2" '[ $RC -eq 2 ]'
check "印出 job 層級的逾時訊息" 'echo "$OUT" | grep -q "\[Job canceled\] deploy：JOB-TIMEOUT-MSG"'
check "被取消的步驟也抓 log" 'echo "$OUT" | grep -q "canceled 的步驟：upload（log 9）" && echo "$OUT" | grep -q "UPLOAD-TAIL-LINE"'
check "不再誤報「沒有失敗或被取消的步驟」" '! echo "$OUT" | grep -q "沒有失敗或被取消的步驟"'

scenario "情境 2c：驗證階段失敗 → 印出 validationResults，timeline 204 不當成連線錯誤" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-invalid.json' $'GET\t/builds/101/timeline\t204\tempty.txt'
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 2（建置失敗，不是連線錯誤的 1）" '[ $RC -eq 2 ]'
check "印出 error 等級的驗證訊息" 'echo "$OUT" | grep -q "\[驗證錯誤\] VALIDATION-ERR-MSG secureFile deploy-key"'
check "warning 等級的驗證訊息不印" '! echo "$OUT" | grep -q "VALIDATION-WARN-MSG"'
check "說明 timeline 為空、派工前就失敗" 'echo "$OUT" | grep -q "timeline 為空：建置在派工前就失敗，見上方驗證訊息"'
check "不再印「取得 timeline 失敗」" '! echo "$OUT" | grep -q "取得 timeline 失敗"'
check "沒有去抓任何 log" '! grep -q "/logs/" "$FAKE_LOG"'

scenario "情境 2d：成功的建置不印驗證訊息" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-ok-warn.json'
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 2>&1)"; RC=$?
check "exit 0 且沒有驗證訊息段落" '[ $RC -eq 0 ] && ! echo "$OUT" | grep -q "validationResults" && ! echo "$OUT" | grep -q SHOULD-NOT-PRINT'

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

# ---------------------------------------------------------------- 情境 9～10：--resource-ref、--wait-build
scenario "情境 9a：沒帶新參數時 body 與原本完全相同" "$R_PIPES" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-ok.json'
bash "$RUN" "${args[@]}" --pipeline "app CI" --branch main -p offline=false >/dev/null 2>&1; RC=$?
BODY="$(grep "^POST" "$FAKE_LOG" | cut -f3)"
check "exit 0，body 只有 self 與 templateParameters（逐鍵相等）" '[ $RC -eq 0 ] && echo "$BODY" | python3 -c "import json,sys; assert json.load(sys.stdin)=={\"resources\":{\"repositories\":{\"self\":{\"refName\":\"refs/heads/main\"}}},\"templateParameters\":{\"offline\":\"false\"}}"'
check "請求順序：查清單 → 觸發 → 查建置" '[ "$(cut -f1,2 "$FAKE_LOG" | sed "s/?.*//" | paste -sd" " -)" = "GET	http://ado.example:8080/Coll/proj-x/_apis/pipelines POST	http://ado.example:8080/Coll/proj-x/_apis/pipelines/7/runs GET	http://ado.example:8080/Coll/proj-x/_apis/build/builds/101" ]'
scenario "情境 9a2：不帶分支也不帶參數 → body 是空物件" "$R_RUN"
bash "$RUN" "${args[@]}" --pipeline 7 --no-wait >/dev/null 2>&1; RC=$?
check "exit 0 且 body 為 {}" '[ $RC -eq 0 ] && [ "$(grep "^POST" "$FAKE_LOG" | cut -f3)" = "{}" ]'

scenario "情境 9b：--resource-ref 指定被引用 repo 的分支" "$R_RUN" $'GET\t/build/builds/101?\t200\tb-ok.json'
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 --branch main --resource-ref core=UAT --resource-ref tools=refs/heads/dev -p offline=false 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
BODY="$(grep "^POST" "$FAKE_LOG" | cut -f3)"
check "exit 0 並 POST 到 pipelines/7/runs" '[ $RC -eq 0 ] && grep -q "^POST	http://ado.example:8080/Coll/proj-x/_apis/pipelines/7/runs?api-version=6.0-preview.1	" "$FAKE_LOG"'
check "body：self 與兩個別名的 refName（UAT 補成 refs/heads/UAT）＋ templateParameters" 'echo "$BODY" | python3 -c "import json,sys; assert json.load(sys.stdin)=={\"resources\":{\"repositories\":{\"self\":{\"refName\":\"refs/heads/main\"},\"core\":{\"refName\":\"refs/heads/UAT\"},\"tools\":{\"refName\":\"refs/heads/dev\"}}},\"templateParameters\":{\"offline\":\"false\"}}"'

scenario "情境 9c：只給 --resource-ref、不給 --branch → 不帶 self" "$R_RUN"
bash "$RUN" "${args[@]}" --pipeline 7 --resource-ref core=UAT --no-wait >/dev/null 2>&1; RC=$?
check "exit 0 且 body 只有 core" '[ $RC -eq 0 ] && grep "^POST" "$FAKE_LOG" | cut -f3 | python3 -c "import json,sys; assert json.load(sys.stdin)=={\"resources\":{\"repositories\":{\"core\":{\"refName\":\"refs/heads/UAT\"}}}}"'

scenario "情境 9d：--resource-ref 格式錯誤不發請求"
bash "$RUN" "${args[@]}" --pipeline 7 --resource-ref noequals >/dev/null 2>&1; RC1=$?
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 --resource-ref self=main 2>&1)"; RC2=$?
check "沒有 = → exit 1；self 要用 --branch → exit 1；都沒有發出請求" '[ $RC1 -eq 1 ] && [ $RC2 -eq 1 ] && echo "$OUT" | grep -q "self 的分支請用 --branch" && [ ! -s "$FAKE_LOG" ]'

scenario "情境 9e：同一別名重複指定 --resource-ref → 報錯、不發請求"
OUT="$(bash "$RUN" "${args[@]}" --pipeline 7 --resource-ref core=A --resource-ref core=B 2>&1)"; RC=$?
check "exit 1、說明別名重複、沒有發出請求" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "別名「core」重複指定" && [ ! -s "$FAKE_LOG" ]'
scenario "情境 9f：不同別名（其中一個是另一個的前綴）不算重複" "$R_RUN"
bash "$RUN" "${args[@]}" --pipeline 7 --resource-ref core=A --resource-ref core2=B --no-wait >/dev/null 2>&1; RC=$?
check "exit 0 且兩個別名都在 body" '[ $RC -eq 0 ] && grep "^POST" "$FAKE_LOG" | cut -f3 | python3 -c "import json,sys; r=json.load(sys.stdin)[\"resources\"][\"repositories\"]; assert r=={\"core\":{\"refName\":\"refs/heads/A\"},\"core2\":{\"refName\":\"refs/heads/B\"}}"'

echo '{"id":555,"status":"completed","result":"failed"}' >| "$T/r/b555-fail.json"
echo '{"id":555,"status":"inProgress"}' >| "$T/r/b555-progress.json"
echo '{"id":555,"status":"completed","result":"succeeded"}' >| "$T/r/b555-ok.json"
scenario "情境 10a：--wait-build 不觸發，只輪詢，失敗時沿用失敗輸出" \
  $'GET\t/build/builds/555?\t200\tb555-progress.json,b555-fail.json' \
  $'GET\t/builds/555/timeline\t200\ttimeline.json' \
  $'GET\t/builds/555/logs/5\t200\tlog5.txt'
OUT="$(bash "$RUN" "${args[@]}" --wait-build 555 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 2" '[ $RC -eq 2 ]'
check "沒有 POST、沒有查 pipeline 清單" '! grep -q "^POST" "$FAKE_LOG" && ! grep -q "/_apis/pipelines" "$FAKE_LOG"'
check "輪詢建置 555 兩次" '[ "$(grep -c "^GET	.*/build/builds/555?" "$FAKE_LOG")" -eq 2 ]'
check "印出失敗步驟與 log 末段" 'echo "$OUT" | grep -q "failed 的步驟：Gradle 建置（log 5）" && echo "$OUT" | grep -q "ERROR-LINE-XYZ" && echo "$OUT" | grep -q "結果：failed（run 555）"'

scenario "情境 10b：--wait-build 成功 → exit 0" $'GET\t/build/builds/555?\t200\tb555-ok.json'
OUT="$(bash "$RUN" "${args[@]}" --wait-build 555 2>&1)"; RC=$?
check "exit 0 並印出成功" '[ $RC -eq 0 ] && echo "$OUT" | grep -q "結果：成功（run 555）" && [ "$(wc -l < "$FAKE_LOG")" -eq 1 ]'

scenario "情境 10c：--wait-build 參數錯誤不發請求"
bash "$RUN" "${args[@]}" --wait-build abc >/dev/null 2>&1; RC1=$?
bash "$RUN" "${args[@]}" --wait-build 555 --pipeline 7 >/dev/null 2>&1; RC2=$?
bash "$RUN" "${args[@]}" --wait-build 555 --no-wait >/dev/null 2>&1; RC3=$?
check "非數字、與 --pipeline 或 --no-wait 並用都 exit 1，且沒有發出請求" '[ $RC1 -eq 1 ] && [ $RC2 -eq 1 ] && [ $RC3 -eq 1 ] && [ ! -s "$FAKE_LOG" ]'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
