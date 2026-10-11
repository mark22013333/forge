#!/usr/bin/env bash
# ado-authorize.sh 的離線測試：PATH 前放一支假 curl，依 URL 回固定 JSON，並記錄每個請求，不連任何網路。
# 執行：bash plugins/ci-tools/tests/ado-authorize.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
AUTH="$HERE/../scripts/ado-authorize.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ---------------------------------------------------------------- 假 curl（與 ado-run.test.sh 相同的路由機制）
# 路由檔每行：方法<TAB>URL 片段<TAB>HTTP 碼<TAB>回應檔（多個以逗號分隔，依呼叫次數輪流、停在最後一個）
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

echo '{"count":1,"value":[{"id":"sf-111","name":"deploy.key"},{"id":"sf-222","name":"other.key"}]}' >| "$T/r/securefiles.json"
echo '{"count":1,"value":[{"id":9,"name":"Default"}]}' >| "$T/r/queues.json"
echo '{"id":"proj-guid-1","name":"proj-x"}' >| "$T/r/project.json"
echo '{"id":"repo-guid-2","name":"core lib"}' >| "$T/r/repo.json"
echo '{"resource":{"type":"securefile","id":"sf-111"},"pipelines":[{"id":3,"authorized":true},{"id":4,"authorized":true}]}' >| "$T/r/perm-34.json"
echo '{"resource":{"type":"queue","id":"9"},"pipelines":[{"id":3,"authorized":true}]}' >| "$T/r/perm-3.json"
echo '{"resource":{"type":"securefile","id":"sf-111"},"pipelines":[]}' >| "$T/r/perm-none.json"
echo '{"resource":{"type":"securefile","id":"sf-111"},"allPipelines":{"authorized":true},"pipelines":[]}' >| "$T/r/perm-all.json"
echo '{}' >| "$T/r/empty.json"

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
export EX_PAT="secret-token-456"

scenario() {
  echo "== $1"; shift
  S="$T/s$((++SN))"; mkdir -p "$S/state"
  export FAKE_LOG="$S/log" FAKE_STDIN="$S/stdin" FAKE_ARGV="$S/argv" FAKE_STATE="$S/state" FAKE_ROUTES="$S/routes"
  : >| "$FAKE_LOG"; : >| "$FAKE_STDIN"; : >| "$FAKE_ARGV"
  printf '%s\n' "$@" >| "$FAKE_ROUTES"
}
SN=0
R_SF=$'GET\t/distributedtask/securefiles?\t200\tsecurefiles.json'
R_Q=$'GET\t/distributedtask/queues?queueName=Default\t200\tqueues.json'
R_PATCH=$'PATCH\t/pipelinePermissions/\t200\tempty.json'
args=(--config "$T/.ci-tools.yml" --servers "$T/servers.yml")
patch_body() { grep "^PATCH" "$FAKE_LOG" | cut -f3; }

scenario "情境 1：list secure file" "$R_SF" $'GET\t/pipelinePermissions/securefile/sf-111\t200\tperm-34.json'
OUT="$(bash "$AUTH" list --securefile deploy.key "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "名稱轉 id 並列出 3,4" 'echo "$OUT" | grep -q "（sf-111）已授權的 pipeline：3,4"'
check "用 6.1-preview.1 查詢授權" 'grep -q "/proj-x/_apis/pipelines/pipelinePermissions/securefile/sf-111?api-version=6.1-preview.1" "$FAKE_LOG"'
check "list 只發 GET" '! grep -qvE "^GET" "$FAKE_LOG"'
check "權杖經 stdin、不在參數、不印出" 'grep -q secret-token-456 "$FAKE_STDIN" && ! grep -q secret-token-456 "$FAKE_ARGV" && ! echo "$OUT" | grep -q secret-token-456'

scenario "情境 2：grant 佇列並讀回成功" "$R_Q" "$R_PATCH" $'GET\t/pipelinePermissions/queue/9\t200\tperm-34.json'
OUT="$(bash "$AUTH" grant --queue Default --pipelines 3,4 "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ] && echo "$OUT" | grep -q "已讀回確認"'
check "PATCH 到 queue/9" 'grep -q "^PATCH	http://ado.example:8080/Coll/proj-x/_apis/pipelines/pipelinePermissions/queue/9?api-version=6.1-preview.1" "$FAKE_LOG"'
check "body 為兩個 authorized:true（數字 id）" 'patch_body | python3 -c "import json,sys; assert json.load(sys.stdin)=={\"pipelines\":[{\"id\":3,\"authorized\":True},{\"id\":4,\"authorized\":True}]}"'
check "寫入後有讀回" '[ "$(grep -c "^GET.*pipelinePermissions/queue/9" "$FAKE_LOG")" -ge 1 ]'

scenario "情境 3：grant 後讀回少一個 → exit 2" "$R_Q" "$R_PATCH" $'GET\t/pipelinePermissions/queue/9\t200\tperm-3.json'
OUT="$(bash "$AUTH" grant --queue Default --pipelines 3,4 "${args[@]}" 2>&1)"; RC=$?
check "exit 2 並指出 pipeline 4" '[ $RC -eq 2 ] && echo "$OUT" | grep -q "pipeline 4"'

scenario "情境 4：revoke secure file" "$R_SF" "$R_PATCH" $'GET\t/pipelinePermissions/securefile/sf-111\t200\tperm-none.json'
OUT="$(bash "$AUTH" revoke --securefile deploy.key --pipelines 3,4 "${args[@]}" 2>&1)"; RC=$?
check "exit 0" '[ $RC -eq 0 ]'
check "body 為 authorized:false" 'patch_body | python3 -c "import json,sys; d=json.load(sys.stdin); assert [p[\"authorized\"] for p in d[\"pipelines\"]]==[False,False]"'
check "讀回顯示（無）" 'echo "$OUT" | grep -q "讀回已授權的 pipeline：（無）"'

scenario "情境 5：repo 資源 id ＝ 專案 id.repo id（名稱含空白要編碼）" $'GET\t/_apis/projects/proj-x\t200\tproject.json' $'GET\t/git/repositories/core%20lib\t200\trepo.json' "$R_PATCH" $'GET\t/pipelinePermissions/repository/\t200\tperm-34.json'
OUT="$(bash "$AUTH" grant --repo "core lib" --pipelines 4 "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "repo 名稱以 %20 編碼" 'grep -q "/git/repositories/core%20lib?" "$FAKE_LOG"'
check "PATCH 到 repository/proj-guid-1.repo-guid-2" 'grep -q "^PATCH	.*/pipelinePermissions/repository/proj-guid-1.repo-guid-2?" "$FAKE_LOG"'

scenario "情境 6：資源設為所有 pipeline 可用時，revoke 無效 → exit 2 並說明" "$R_SF" "$R_PATCH" $'GET\t/pipelinePermissions/securefile/sf-111\t200\tperm-all.json'
OUT="$(bash "$AUTH" revoke --securefile deploy.key --pipelines 3 "${args[@]}" 2>&1)"; RC=$?
check "exit 2 並提到所有 pipeline" '[ $RC -eq 2 ] && echo "$OUT" | grep -q "所有 pipeline"'

scenario "情境 7：PATCH 回 401" "$R_SF" $'PATCH\t/pipelinePermissions/\t401\tempty.json'
OUT="$(bash "$AUTH" grant --securefile deploy.key --pipelines 3 "${args[@]}" 2>&1)"; RC=$?
check "exit 1 並說明權杖範圍" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "HTTP 401" && echo "$OUT" | grep -q "踩坑 32"'

scenario "情境 8：資源不存在、參數錯誤" "$R_SF"
OUT="$(bash "$AUTH" list --securefile nope.key "${args[@]}" 2>&1)"; RC=$?
check "找不到 secure file → exit 1" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "找不到 securefile「nope.key」"'
: >| "$FAKE_LOG"
bash "$AUTH" grant --securefile deploy.key --pipelines a,b "${args[@]}" >/dev/null 2>&1; RC=$?
check "--pipelines 不是數字 → exit 1" '[ $RC -eq 1 ]'
bash "$AUTH" grant --securefile x --queue y --pipelines 3 "${args[@]}" >/dev/null 2>&1; RC=$?
check "同時給兩種資源 → exit 1" '[ $RC -eq 1 ]'
bash "$AUTH" remove --queue y "${args[@]}" >/dev/null 2>&1; RC=$?
check "未知子命令 → exit 1" '[ $RC -eq 1 ]'
check "參數錯誤時沒有發出請求" '[ ! -s "$FAKE_LOG" ]'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
