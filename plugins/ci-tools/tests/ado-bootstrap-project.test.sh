#!/usr/bin/env bash
# ado-bootstrap-project.sh 的離線測試：PATH 前放一支假 curl，依方法＋URL 回固定 JSON 與狀態碼，並記錄每個請求，不連任何網路。
# 執行：bash plugins/ci-tools/tests/ado-bootstrap-project.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
BOOT="$HERE/../scripts/ado-bootstrap-project.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ---------------------------------------------------------------- 假 curl
# 路由檔每行：方法<TAB>URL 片段<TAB>HTTP 碼<TAB>回應（多個以逗號分隔，依呼叫次數輪流、停在最後一個）
# 回應可寫成「碼:檔名」覆寫該次的 HTTP 碼（例：404:empty.json,200:project.json）。第一個符合的路由生效。
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
  f="${arr[$i]}"
  case "$f" in [0-9][0-9][0-9]:*) code="${f%%:*}"; f="${f#*:}" ;; esac
  cat "$FAKE_DIR/$f" >| "$out"
  printf '%s' "$code"
  exit 0
done < "$FAKE_ROUTES"
: >| "$out"; printf '404'
EOF
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH" FAKE_DIR="$T/r"
mkdir -p "$T/r"

r() { printf '%s\n' "$2" >| "$T/r/$1"; }
r empty.json '{}'
r processes.json '{"value":[{"id":"tpl-agile","name":"Agile","isDefault":false},{"id":"tpl-basic","name":"Basic","isDefault":true}]}'
r project.json '{"id":"proj-guid-1","name":"proj-x"}'
r op-queued.json '{"id":"op-1","status":"queued"}'
r op-progress.json '{"id":"op-1","status":"inProgress"}'
r op-done.json '{"id":"op-1","status":"succeeded"}'
r repos-default.json '{"value":[{"id":"def-id","name":"proj-x"}]}'
r repos-after.json '{"value":[{"id":"def-id","name":"app-main"},{"id":"rid-core","name":"core-lib"}]}'
r repos-full.json '{"value":[{"id":"rid-main","name":"app-main","defaultBranch":"refs/heads/uat"},{"id":"rid-core","name":"core-lib","defaultBranch":"refs/heads/main"},{"id":"rid-x","name":"core-lib.git"}]}'
r repos-ok.json '{"value":[{"id":"rid-main","name":"app-main","defaultBranch":"refs/heads/main"},{"id":"rid-core","name":"core-lib","defaultBranch":"refs/heads/main"}]}'
r repo-renamed.json '{"id":"def-id","name":"app-main"}'
r repo-created.json '{"id":"rid-core","name":"core-lib"}'
r refs-0.json '{"count":0,"value":[]}'
r refs-2.json '{"count":2,"value":[{"name":"refs/heads/main"},{"name":"refs/heads/uat"}]}'
r refs-main.json '{"count":1,"value":[{"name":"refs/heads/main"}]}'
r main-uat.json '{"id":"rid-main","name":"app-main","defaultBranch":"refs/heads/uat"}'
r main-main.json '{"id":"rid-main","name":"app-main","defaultBranch":"refs/heads/main"}'
r core-main.json '{"id":"rid-core","name":"core-lib","defaultBranch":"refs/heads/main"}'
r items.json '{"path":"/azure-pipelines.yml","content":"resources:\n  repositories:\n  - repository: core\n    type: git\n    name: proj-x/core-lib   # 依賴 repo\nsteps:\n- task: DownloadSecureFile@1\n  inputs:\n    secureFile: deploy.key\n"}'
r items-noref.json '{"path":"/azure-pipelines.yml","content":"resources:\n  repositories:\n  - repository: tools\n    type: git\n    name: proj-x/core-lib-tools\nsteps:\n- script: echo web\n"}'
r repos-two.json '{"value":[{"id":"rid-main","name":"app-main","defaultBranch":"refs/heads/main"},{"id":"rid-web","name":"app-web","defaultBranch":"refs/heads/main"},{"id":"rid-core","name":"core-lib","defaultBranch":"refs/heads/main"}]}'
r web-main.json '{"id":"rid-web","name":"app-web","defaultBranch":"refs/heads/main"}'
r pipes-79.json '{"count":2,"value":[{"id":7,"name":"app-main CI"},{"id":9,"name":"app-web CI"}]}'
r pipe-9.json '{"id":9,"name":"app-web CI","configuration":{"type":"yaml","path":"/azure-pipelines.yml","repository":{"id":"rid-web","type":"azureReposGit"}}}'
r perm-79.json '{"pipelines":[{"id":7,"authorized":true},{"id":9,"authorized":true}]}'
r pipes-none.json '{"count":0,"value":[]}'
r pipes-7.json '{"count":1,"value":[{"id":7,"name":"app-main CI"}]}'
r pipes-8.json '{"count":1,"value":[{"id":8,"name":"app-main CI"}]}'
r pipe-7.json '{"id":7,"name":"app-main CI","configuration":{"type":"yaml","path":"azure-pipelines.yml","repository":{"id":"rid-main","type":"azureReposGit"}}}'
r pipe-8.json '{"id":8,"name":"app-main CI","configuration":{"type":"yaml","path":"/azure-pipelines.yml","repository":{"id":"rid-main","type":"azureReposGit"}}}'
r queues.json '{"count":1,"value":[{"id":9,"name":"Default"}]}'
r perm-7.json '{"pipelines":[{"id":7,"authorized":true}]}'
r perm-8.json '{"pipelines":[{"id":8,"authorized":true}]}'
r perm-none.json '{"pipelines":[]}'
r types-git.json '{"count":1,"value":[{"name":"git","displayName":"Other Git"}]}'
r ep.json '{"id":"ep-1","name":"ci-tools-import-app-main"}'
r ir.json '{"importRequestId":5,"status":"queued"}'
r ir-queued.json '{"importRequestId":5,"status":"inProgress"}'
r ir-done.json '{"importRequestId":5,"status":"completed"}'
r ir-failed.json '{"importRequestId":5,"status":"failed","detailedStatus":{"errorMessage":"authentication failed"}}'

# servers.yml：目前的 server 與一台舊 server（環境檢查要找出舊主機名）
cat >| "$T/servers.yml" <<'EOF'
default_server: ex-ado
servers:
  ex-ado:
    url: http://ado.example:8080
    collection: Coll
    api_version: "6.0"
    pat_env: EX_PAT
    agents:
      - name: a1
        jdk:
          JDK17: 'C:\Java\jdk-17'
        gradle_user_home: 'D:\cache\gradle'
        npm_cache: 'D:\cache\npm'
  old-ado:
    url: http://old-ado.example:8080
    collection: Coll
    pat_env: EX_PAT
EOF
# 工作區：.ci-tools.yml 放在主 repo 目錄，上一層＋repo 名稱就是各 repo 的本機目錄
mkdir -p "$T/ws/app-main/bin" "$T/ws/core-lib"
cat >| "$T/ws/app-main/.ci-tools.yml" <<'EOF'
server: ex-ado
ado_project: proj-x
repos:
  - name: app-main
    role: main
    gitea_url: https://git.example/team/app-main.git
    branches: [main, uat]
  - name: core-lib
    role: dependency
    gitea_url: https://git.example/team/core-lib.git
    branches: [main]
EOF
cat >| "$T/ws/app-main/azure-pipelines.yml" <<'EOF'
variables:
  JAVA_HOME: 'C:\Java\jdk-21'
  GRADLE_USER_HOME: 'D:\cache\gradle'
steps:
- script: echo build
EOF
printf '#!/bin/sh\ncurl http://old-ado.example:8080/Coll/_apis/projects\n' >| "$T/ws/app-main/bin/deploy.sh"
export EX_PAT="secret-ado-token-123"
unset GITEA_USER GITEA_TOKEN

scenario() {
  echo "== $1"; shift
  S="$T/s$((++SN))"; mkdir -p "$S/state"
  export FAKE_LOG="$S/log" FAKE_STDIN="$S/stdin" FAKE_ARGV="$S/argv" FAKE_STATE="$S/state" FAKE_ROUTES="$S/routes"
  : >| "$FAKE_LOG"; : >| "$FAKE_STDIN"; : >| "$FAKE_ARGV"
  printf '%s\n' "$@" >| "$FAKE_ROUTES"
}
SN=0
args=(--config "$T/ws/app-main/.ci-tools.yml" --servers "$T/servers.yml" --poll 0)
body_of() { grep "^$1	[^	]*$2" "$FAKE_LOG" | head -1 | cut -f3; }
R_PROJ=$'GET\t/_apis/projects/proj-x?\t200\tproject.json'
# 授權（ado-authorize.sh）用到的路由
R_Q=$'GET\t/distributedtask/queues?queueName=Default\t200\tqueues.json'
R_CORE=$'GET\t/git/repositories/core-lib?\t200\tcore-main.json'
R_PERM_PATCH=$'PATCH\t/pipelinePermissions/\t200\tempty.json'

# ---------------------------------------------------------------- 情境 1：預覽、專案不存在
scenario "情境 1：預覽模式，專案不存在" \
  $'GET\t/_apis/projects/proj-x?\t404\tempty.json' \
  $'GET\t/_apis/process/processes?\t200\tprocesses.json'
OUT="$(bash "$BOOT" "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "預覽只發 GET（沒有 POST/PATCH/DELETE）" '[ -s "$FAKE_LOG" ] && ! grep -qvE "^GET	" "$FAKE_LOG"'
check "預告建立專案並用預設流程範本 Basic" 'echo "$OUT" | grep -q "\[預覽\] 將建立專案「proj-x」（Git，流程範本 Basic）"'
check "預告預設 repo 改名給第一個 main" 'echo "$OUT" | grep -q "改名為「app-main」"'
check "預告建立依賴 repo" 'echo "$OUT" | grep -q "建立 repo「core-lib」（dependency）"'
check "環境檢查：JAVA_HOME 與 agents 不一致時警告" 'echo "$OUT" | grep -q "警告：app-main/azure-pipelines.yml:2 JAVA_HOME=C:\\\\Java\\\\jdk-21 與 servers.yml 的 ex-ado agents 不一致"'
check "環境檢查：GRADLE_USER_HOME 一致時不警告" '! echo "$OUT" | grep -q "GRADLE_USER_HOME=.*不一致"'
check "環境檢查：一致的也印出比對結果（map 形式）" 'echo "$OUT" | grep -q "app-main/azure-pipelines.yml：JAVA_HOME＝C:\\\\Java\\\\jdk-21（與 agent 不一致）、GRADLE_USER_HOME＝D:\\\\cache\\\\gradle（與 agent 一致）、NPM_CONFIG_CACHE 未設定"'
check "環境檢查：列出含其他 server 主機名的位置" 'echo "$OUT" | grep -q "app-main/bin/deploy.sh:2 含其他 server（old-ado）的主機 old-ado.example"'
check "提示用 sync-all-repos.sh 推送" 'echo "$OUT" | grep -q "sync-all-repos.sh --config"'
check "ADO 權杖經 stdin、不在參數、不印出" 'grep -q secret-ado-token-123 "$FAKE_STDIN" && ! grep -q secret-ado-token-123 "$FAKE_ARGV" && ! echo "$OUT" | grep -q secret-ado-token-123'

# ---------------------------------------------------------------- 情境 2：--apply 建專案、改名、建 repo
scenario "情境 2：--apply 建立專案（輪詢 operation）、預設 repo 改名、建立依賴 repo" \
  $'GET\t/_apis/projects/proj-x?\t200\t404:empty.json,200:project.json' \
  $'GET\t/_apis/process/processes?\t200\tprocesses.json' \
  $'POST\t/_apis/projects?\t202\top-queued.json' \
  $'GET\t/_apis/operations/op-1?\t200\top-progress.json,op-done.json' \
  $'GET\t/proj-x/_apis/git/repositories?\t200\trepos-default.json,repos-after.json' \
  $'GET\t/git/repositories/def-id/refs?\t200\trefs-0.json' \
  $'GET\t/git/repositories/rid-core/refs?\t200\trefs-0.json' \
  $'PATCH\t/git/repositories/def-id?\t200\trepo-renamed.json' \
  $'GET\t/git/repositories/def-id?\t200\trepo-renamed.json' \
  $'POST\t/proj-x/_apis/git/repositories?\t201\trepo-created.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-none.json'
OUT="$(bash "$BOOT" --apply "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "POST 專案帶預設流程範本 id" 'body_of POST "/_apis/projects?" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"name\"]==\"proj-x\" and d[\"capabilities\"][\"processTemplate\"][\"templateTypeId\"]==\"tpl-basic\" and d[\"capabilities\"][\"versioncontrol\"][\"sourceControlType\"]==\"Git\""'
check "輪詢 operation 到 succeeded（2 次）" '[ "$(grep -c "^GET	.*/_apis/operations/op-1?" "$FAKE_LOG")" -eq 2 ]'
check "改名 PATCH 用 repo id（不是名稱）" 'grep -q "^PATCH	http://ado.example:8080/Coll/proj-x/_apis/git/repositories/def-id?" "$FAKE_LOG" && ! grep -qE "^PATCH	[^	]*/git/repositories/(proj-x|app-main)\?" "$FAKE_LOG"'
check "改名 body 是第一個 main repo 的名稱" '[ "$(body_of PATCH /git/repositories/def-id)" = "{\"name\": \"app-main\"}" ]'
check "依賴 repo 用 POST 建立並帶專案 id" 'body_of POST "/git/repositories?" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d=={\"name\":\"core-lib\",\"project\":{\"id\":\"proj-guid-1\"}}"'
check "只 POST 一個 repo（主 repo 用改名，不另建）" '[ "$(grep -c "^POST	.*/git/repositories?" "$FAKE_LOG")" -eq 1 ]'
check "空 repo 不改預設分支" '! echo "$OUT" | grep -q "預設分支改成"'

# ---------------------------------------------------------------- 情境 3：預設分支不對、pipeline 已存在
scenario "情境 3：--apply 預設分支不對要 PATCH；pipeline 已存在不重建；授權佇列與依賴 repo" \
  "$R_PROJ" \
  $'GET\t/proj-x/_apis/git/repositories?\t200\trepos-full.json' \
  $'GET\t/git/repositories/rid-main/refs?filter=\t200\trefs-main.json' \
  $'GET\t/git/repositories/rid-core/refs?filter=\t200\trefs-main.json' \
  $'GET\t/refs?api-version\t200\trefs-2.json' \
  $'GET\t/git/repositories/rid-main?\t200\tmain-uat.json,main-main.json' \
  $'GET\t/git/repositories/rid-core?\t200\tcore-main.json' \
  $'PATCH\t/git/repositories/rid-main?\t200\tmain-main.json' \
  $'GET\t/git/repositories/rid-main/items?\t200\titems.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-7.json' \
  $'GET\t/_apis/pipelines/7?\t200\tpipe-7.json' \
  "$R_Q" "$R_CORE" "$R_PERM_PATCH" \
  $'GET\t/pipelinePermissions/\t200\tperm-none.json,perm-7.json,perm-none.json,perm-7.json'
OUT="$(bash "$BOOT" --apply "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "PATCH defaultBranch 到 repo id，值為 branches 第一個" 'grep -q "^PATCH	[^	]*/git/repositories/rid-main?" "$FAKE_LOG" && [ "$(body_of PATCH /git/repositories/rid-main)" = "{\"defaultBranch\": \"refs/heads/main\"}" ]'
check "已是 main 的 core-lib 不 PATCH" '! grep -q "^PATCH	[^	]*/git/repositories/rid-core" "$FAKE_LOG"'
check "pipeline 已存在（路徑不帶 / 也認得）→ 不 POST" '! grep -q "^POST	" "$FAKE_LOG" && echo "$OUT" | grep -q "pipeline（id 7），沿用"'
check "授權佇列與依賴 repo（各一次 PATCH）" '[ "$(grep -c "^PATCH	.*/pipelinePermissions/queue/9?" "$FAKE_LOG")" -eq 1 ] && [ "$(grep -c "^PATCH	.*/pipelinePermissions/repository/proj-guid-1.rid-core?" "$FAKE_LOG")" -eq 1 ]'
check "名稱以 .git 結尾的 repo 會警告" 'echo "$OUT" | grep -q "警告：repo 名稱以 .git 結尾：core-lib.git"'
check "secure file 只提示、不處理" 'echo "$OUT" | grep -q "secure file：deploy.key" && ! grep -q "securefiles" "$FAKE_LOG"'

R_AUTHED=(
  "$R_PROJ"
  $'GET\t/proj-x/_apis/git/repositories?\t200\trepos-ok.json'
  $'GET\t/refs?filter=\t200\trefs-main.json'
  $'GET\t/refs?api-version\t200\trefs-2.json'
  $'GET\t/git/repositories/rid-main?\t200\tmain-main.json'
  $'GET\t/git/repositories/rid-core?\t200\tcore-main.json'
  $'GET\t/git/repositories/rid-main/items?\t200\titems.json'
  $'GET\t/_apis/pipelines?\t200\tpipes-7.json'
  $'GET\t/_apis/pipelines/7?\t200\tpipe-7.json'
  "$R_Q" "$R_CORE" "$R_PERM_PATCH"
  $'GET\t/pipelinePermissions/\t200\tperm-7.json'
)
for mode in preview apply; do
  scenario "情境 3b（${mode}）：佇列與依賴 repo 都已授權 → 不發 PATCH" "${R_AUTHED[@]}"
  if [ "$mode" = apply ]; then OUT="$(bash "$BOOT" --apply "${args[@]}" 2>&1)"; RC=$?; else OUT="$(bash "$BOOT" "${args[@]}" 2>&1)"; RC=$?; fi
  echo "$OUT" | sed -n '/== 6/,/^$/p' | sed 's/^/    | /'
  check "exit 0" '[ $RC -eq 0 ]'
  check "沒有任何 PATCH" '! grep -q "^PATCH" "$FAKE_LOG"'
  check "有先查授權（佇列與 repo 各一次 GET）" '[ "$(grep -c "^GET	.*/pipelinePermissions/" "$FAKE_LOG")" -eq 2 ]'
  check "印出已授權、沿用" '[ "$(echo "$OUT" | grep -c "已授權 pipeline 7，沿用")" -eq 2 ] && ! echo "$OUT" | grep -q "將授權"'
done

scenario "情境 3c（預覽）：只缺授權時才預告授權" "${R_AUTHED[@]/perm-7.json/perm-none.json}"
OUT="$(bash "$BOOT" "${args[@]}" 2>&1)"; RC=$?
check "exit 0、預告授權 pipeline 7、沒有 PATCH" '[ $RC -eq 0 ] && [ "$(echo "$OUT" | grep -c "\[預覽\] 將授權 pipeline 7 使用")" -eq 2 ] && ! grep -q "^PATCH" "$FAKE_LOG"'

# ---------------------------------------------------------------- 情境 4：pipeline POST 回 401，但列表已有
R_OKREPOS=(
  "$R_PROJ"
  $'GET\t/proj-x/_apis/git/repositories?\t200\trepos-ok.json'
  $'GET\t/refs?filter=\t200\trefs-main.json'
  $'GET\t/refs?api-version\t200\trefs-2.json'
  $'GET\t/git/repositories/rid-main?\t200\tmain-main.json'
  $'GET\t/git/repositories/rid-core?\t200\tcore-main.json'
)
scenario "情境 4：建立 pipeline 回 401，重新列出已有 → 視為成功" \
  "${R_OKREPOS[@]}" \
  $'GET\t/git/repositories/rid-main/items?\t200\titems.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-none.json,pipes-8.json' \
  $'GET\t/_apis/pipelines/8?\t200\tpipe-8.json' \
  $'POST\t/_apis/pipelines?\t401\tempty.json' \
  "$R_Q" "$R_CORE" "$R_PERM_PATCH" \
  $'GET\t/pipelinePermissions/\t200\tperm-none.json,perm-8.json,perm-none.json,perm-8.json'
OUT="$(bash "$BOOT" --apply "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0 並說明視為成功" '[ $RC -eq 0 ] && echo "$OUT" | grep -q "重新列出已有 pipeline id 8，視為成功"'
check "POST body：名稱、folder、yaml、azureReposGit" 'body_of POST "/_apis/pipelines?" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d=={\"name\":\"app-main CI\",\"folder\":\"\\\\\",\"configuration\":{\"type\":\"yaml\",\"path\":\"/azure-pipelines.yml\",\"repository\":{\"id\":\"rid-main\",\"name\":\"app-main\",\"type\":\"azureReposGit\"}}}"'
check "POST 後重新列出 pipeline" '[ "$(grep -c "^GET	.*/_apis/pipelines?" "$FAKE_LOG")" -eq 2 ]'
check "只 POST 一次（不重送）" '[ "$(grep -c "^POST	.*/_apis/pipelines?" "$FAKE_LOG")" -eq 1 ]'
check "授權用新的 pipeline id 8" 'grep "^PATCH	.*/pipelinePermissions/queue/9" "$FAKE_LOG" | cut -f3 | grep -q "\"id\": 8"'

scenario "情境 4b：建立 pipeline 回 200，但重新列出沒有 → exit 2" \
  "${R_OKREPOS[@]}" \
  $'GET\t/git/repositories/rid-main/items?\t200\titems.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-none.json' \
  $'POST\t/_apis/pipelines?\t200\tempty.json'
OUT="$(bash "$BOOT" --apply "${args[@]}" 2>&1)"; RC=$?
check "exit 2" '[ $RC -eq 2 ] && echo "$OUT" | grep -q "重新列出時找不到"'

# ---------------------------------------------------------------- 情境 5～7：--import
scenario "情境 5：--import 沒設 GITEA_TOKEN → exit 1 且說明" "$R_PROJ"
OUT="$(GITEA_USER=u bash "$BOOT" --import --apply "${args[@]}" 2>&1)"; RC=$?
check "exit 1 並說明需要 GITEA_USER 與 GITEA_TOKEN" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "需要環境變數 GITEA_USER 與 GITEA_TOKEN"'
check "沒有發出任何請求" '[ ! -s "$FAKE_LOG" ]'

R_EMPTYREPOS=(
  "$R_PROJ"
  $'GET\t/proj-x/_apis/git/repositories?\t200\trepos-ok.json'
  $'GET\t/serviceendpoint/types?type=git\t200\ttypes-git.json'
  $'POST\t/_apis/serviceendpoint/endpoints?\t200\tep.json'
  $'POST\t/importRequests?\t201\tir.json'
  $'DELETE\t/_apis/serviceendpoint/endpoints/ep-1?projectIds=proj-guid-1\t204\tempty.json'
)
scenario "情境 6：--import 成功：建 endpoint → importRequest → 輪詢 completed → 刪 endpoint" \
  "${R_EMPTYREPOS[@]}" \
  $'GET\t/importRequests/5?\t200\tir-queued.json,ir-done.json,ir-queued.json,ir-done.json' \
  $'GET\t/git/repositories/rid-main/refs?api\t200\trefs-0.json,refs-2.json' \
  $'GET\t/git/repositories/rid-core/refs?api\t200\trefs-0.json,refs-2.json' \
  $'GET\t/refs?filter=\t200\trefs-main.json' \
  $'GET\t/git/repositories/rid-main?\t200\tmain-main.json' \
  $'GET\t/git/repositories/rid-core?\t200\tcore-main.json' \
  $'GET\t/git/repositories/rid-main/items?\t404\tempty.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-none.json'
OUT="$(GITEA_USER=gituser GITEA_TOKEN=secret-git-token-789 bash "$BOOT" --import --apply "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "endpoint body：type git、UsernamePassword、來源網址、專案參照" 'body_of POST /serviceendpoint/endpoints | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"type\"]==\"git\" and d[\"url\"]==\"https://git.example/team/app-main.git\" and d[\"authorization\"][\"scheme\"]==\"UsernamePassword\" and d[\"authorization\"][\"parameters\"][\"username\"]==\"gituser\" and d[\"serviceEndpointProjectReferences\"][0][\"projectReference\"][\"id\"]==\"proj-guid-1\""'
check "importRequest 帶 gitSource.url 與 serviceEndpointId，用 repo id 與 6.0-preview.1" 'grep -q "^POST	[^	]*/git/repositories/rid-main/importRequests?api-version=6.0-preview.1" "$FAKE_LOG" && body_of POST rid-main/importRequests | python3 -c "import json,sys; p=json.load(sys.stdin)[\"parameters\"]; assert p[\"gitSource\"][\"url\"]==\"https://git.example/team/app-main.git\" and p[\"serviceEndpointId\"]==\"ep-1\""'
check "兩個 repo 各匯入一次、各刪一次 endpoint" '[ "$(grep -c "^POST	.*/importRequests?" "$FAKE_LOG")" -eq 2 ] && [ "$(grep -c "^DELETE	" "$FAKE_LOG")" -eq 2 ]'
check "刪 endpoint 在輪詢到 completed 之後" '[ "$(grep -n "^DELETE" "$FAKE_LOG" | head -1 | cut -d: -f1)" -gt "$(grep -n "importRequests/5?" "$FAKE_LOG" | sed -n 2p | cut -d: -f1)" ]'
check "Git 權杖不在 curl 參數、不印出" '! grep -q secret-git-token-789 "$FAKE_ARGV" && ! echo "$OUT" | grep -q secret-git-token-789'
check "ADO 權杖不在 curl 參數" '! grep -q secret-ado-token-123 "$FAKE_ARGV"'

scenario "情境 7：--import 失敗：仍刪 endpoint，exit 2，不再處理下一個 repo" \
  "${R_EMPTYREPOS[@]}" \
  $'GET\t/importRequests/5?\t200\tir-failed.json' \
  $'GET\t/refs?api\t200\trefs-0.json'
OUT="$(GITEA_USER=gituser GITEA_TOKEN=secret-git-token-789 bash "$BOOT" --import --apply "${args[@]}" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 2 並印出錯誤訊息" '[ $RC -eq 2 ] && echo "$OUT" | grep -q "匯入 failed：authentication failed"'
check "endpoint 仍被刪除" '[ "$(grep -c "^DELETE	.*/serviceendpoint/endpoints/ep-1?" "$FAKE_LOG")" -eq 1 ] && echo "$OUT" | grep -q "已刪除暫時的 service endpoint"'
check "沒有匯入第二個 repo" '[ "$(grep -c "^POST	.*/importRequests?" "$FAKE_LOG")" -eq 1 ]'

scenario "情境 8：--import 預覽只發 GET" \
  "${R_EMPTYREPOS[@]}" \
  $'GET\t/refs?api\t200\trefs-0.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-none.json'
OUT="$(GITEA_USER=gituser GITEA_TOKEN=secret-git-token-789 bash "$BOOT" --import "${args[@]}" 2>&1)"; RC=$?
check "exit 0 且只發 GET" '[ $RC -eq 0 ] && ! grep -qvE "^GET	" "$FAKE_LOG"'
check "預告匯入" 'echo "$OUT" | grep -q "\[預覽\] 將建立暫時的 Git service endpoint → 匯入 https://git.example/team/app-main.git"'

# ---------------------------------------------------------------- 情境 9：清單形式的 variables
mkdir -p "$T/ws2/app-main"
sed 's/^repos:/repos:/' "$T/ws/app-main/.ci-tools.yml" >| "$T/ws2/app-main/.ci-tools.yml"
cat >| "$T/ws2/app-main/azure-pipelines.yml" <<'EOF'
variables:
  - name: JAVA_HOME
    value: 'C:\Java\jdk-17'
  - name: GRADLE_USER_HOME
    # 共用快取
    value: "D:\other-cache"
  - name: NPM_CONFIG_CACHE
  - name: unrelated
    value: x
steps:
- script: echo build
EOF
scenario "情境 9：清單形式（- name: / value:）的變數也要比對" \
  $'GET\t/_apis/projects/proj-x?\t404\tempty.json' \
  $'GET\t/_apis/process/processes?\t200\tprocesses.json'
OUT="$(bash "$BOOT" --config "$T/ws2/app-main/.ci-tools.yml" --servers "$T/servers.yml" 2>&1)"; RC=$?
echo "$OUT" | sed -n '/== 7/,/^$/p' | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "不再誤報「沒有寫」" '! echo "$OUT" | grep -q "沒有寫 JAVA_HOME"'
check "清單形式 JAVA_HOME 與 agents 相同 → 不警告" '! echo "$OUT" | grep -q "JAVA_HOME="'
check "清單形式：一致時印出「與 agent 一致」，沒值的印「未設定」" 'echo "$OUT" | grep -q "JAVA_HOME＝C:\\\\Java\\\\jdk-17（與 agent 一致）" && echo "$OUT" | grep -q "NPM_CONFIG_CACHE 未設定"'
check "清單形式 GRADLE_USER_HOME 不同 → 警告（行號指向 value 行、值已去引號）" 'echo "$OUT" | grep -q "警告：app-main/azure-pipelines.yml:6 GRADLE_USER_HOME=D:\\\\other-cache 與 servers.yml 的 ex-ado agents 不一致"'
check "沒有 value 的清單項目不會吃到下一個項目的 value" '! echo "$OUT" | grep -q "NPM_CONFIG_CACHE=x"'

# ---------------------------------------------------------------- 情境 10：兩個 main repo，只有一個 yaml 引用依賴 repo
mkdir -p "$T/ws3/app-main"
cat >| "$T/ws3/app-main/.ci-tools.yml" <<'EOF'
server: ex-ado
ado_project: proj-x
repos:
  - name: app-main
    role: main
    gitea_url: https://git.example/team/app-main.git
    branches: [main]
  - name: app-web
    role: main
    gitea_url: https://git.example/team/app-web.git
    branches: [main]
  - name: core-lib
    role: dependency
    gitea_url: https://git.example/team/core-lib.git
    branches: [main]
EOF
scenario "情境 10：依賴 repo 只授權給 yaml 有引用它的 pipeline" \
  "$R_PROJ" \
  $'GET\t/proj-x/_apis/git/repositories?\t200\trepos-two.json' \
  $'GET\t/refs?filter=\t200\trefs-main.json' \
  $'GET\t/refs?api-version\t200\trefs-2.json' \
  $'GET\t/git/repositories/rid-main?\t200\tmain-main.json' \
  $'GET\t/git/repositories/rid-web?\t200\tweb-main.json' \
  $'GET\t/git/repositories/rid-core?\t200\tcore-main.json' \
  $'GET\t/git/repositories/rid-main/items?\t200\titems.json' \
  $'GET\t/git/repositories/rid-web/items?\t200\titems-noref.json' \
  $'GET\t/_apis/pipelines?\t200\tpipes-79.json' \
  $'GET\t/_apis/pipelines/7?\t200\tpipe-7.json' \
  $'GET\t/_apis/pipelines/9?\t200\tpipe-9.json' \
  "$R_Q" "$R_CORE" "$R_PERM_PATCH" \
  $'GET\t/pipelinePermissions/queue/\t200\tperm-none.json,perm-79.json' \
  $'GET\t/pipelinePermissions/repository/\t200\tperm-none.json,perm-7.json'
OUT="$(bash "$BOOT" --apply --config "$T/ws3/app-main/.ci-tools.yml" --servers "$T/servers.yml" --poll 0 2>&1)"; RC=$?
echo "$OUT" | sed -n '/== 6/,/^$/p;/== 7/,/^$/p' | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "佇列授權給兩個 pipeline" 'grep "^PATCH	.*/pipelinePermissions/queue/9?" "$FAKE_LOG" | cut -f3 | python3 -c "import json,sys; assert sorted(p[\"id\"] for p in json.load(sys.stdin)[\"pipelines\"])==[7,9]"'
check "依賴 repo 只授權給 pipeline 7" 'grep "^PATCH	.*/pipelinePermissions/repository/" "$FAKE_LOG" | cut -f3 | python3 -c "import json,sys; assert [p[\"id\"] for p in json.load(sys.stdin)[\"pipelines\"]]==[7]"'
check "說明 pipeline 9 沒有引用（名稱只是前綴相同也不算）" 'echo "$OUT" | grep -q "pipeline 9（app-web）的 yaml（ADO 預設分支）沒有引用它，不授權"'
check "說明 pipeline 7 有引用" 'echo "$OUT" | grep -q "pipeline 7（app-main）的 yaml（ADO 預設分支）在 resources.repositories 引用它"'
check "本機沒有 yaml 時印「找不到，略過」" 'echo "$OUT" | grep -q "app-main/azure-pipelines.yml：找不到" && echo "$OUT" | grep -q "app-web/azure-pipelines.yml：找不到"'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
