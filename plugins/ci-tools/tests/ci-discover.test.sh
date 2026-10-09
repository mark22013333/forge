#!/usr/bin/env bash
# ci-discover.sh 的離線測試：假的 servers.yml、假的兄弟專案目錄、假的 ~/.ssh。
# 需要網路的部分改連本機 127.0.0.1 上臨時起的靜態 HTTP 伺服器（模擬 Gitea／GitLab／ADO 的 GET 回應），
# 不連任何外部主機。
# 執行：bash plugins/ci-tools/tests/ci-discover.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）；臨時 HTTP 伺服器在結束時停止。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034,SC2001
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
DISC="$HERE/../scripts/ci-discover.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
show() { printf '%s\n' "$1" | sed 's/^/    | /'; }

# ---------------------------------------------------------------- 臨時 HTTP 伺服器（只綁 127.0.0.1）
PIDS=()
serve() {  # serve <根目錄> <port 檔>
  python3 - "$1" "$2" <<'PY' >/dev/null 2>&1 &
import functools, http.server, sys
root, portfile = sys.argv[1], sys.argv[2]
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=root))
open(portfile, "w").write(str(srv.server_address[1]))
srv.serve_forever()
PY
  PIDS+=($!)
  disown "$!" 2>/dev/null || true   # 結束時 kill 不要印出 Terminated
  local _
  for _ in 1 2 3 4 5 6 7 8 9 10; do [ -s "$2" ] && return 0; sleep 0.3; done
  echo "臨時 HTTP 伺服器沒有起來" >&2; return 1
}
stop_servers() { local p; for p in "${PIDS[@]+"${PIDS[@]}"}"; do kill "$p" 2>/dev/null; done; }
trap stop_servers EXIT

# Gitea＋ADO 模擬：/api/v1/version、/Coll/_apis/projects、/Coll/_apis/distributedtask/pools
mkdir -p "$T/www1/api/v1" "$T/www1/Coll/_apis/distributedtask"
echo '{"version":"1.21.0"}' >| "$T/www1/api/v1/version"
echo '{"count":2,"value":[{"name":"project-a"},{"name":"other-proj"}]}' >| "$T/www1/Coll/_apis/projects"
echo '{"count":1,"value":[{"name":"Default"}]}' >| "$T/www1/Coll/_apis/distributedtask/pools"
serve "$T/www1" "$T/port1" || exit 1
P1="$(cat "$T/port1")"
# GitLab 模擬：只有 /api/v4/version
mkdir -p "$T/www2/api/v4"
echo '{"version":"16.0.0"}' >| "$T/www2/api/v4/version"
serve "$T/www2" "$T/port2" || exit 1
P2="$(cat "$T/port2")"

# ---------------------------------------------------------------- 假的工作區
WS="$T/ws"
mkdir -p "$WS/proj-a/gradle/wrapper" "$WS/proj-b" "$T/extra/group/proj-c" "$T/ssh"
cat >| "$WS/proj-a/.ci-tools.yml" <<EOF
server: home
ado_project: project-a
repos:
  - name: proj-a
    gitea_url: http://127.0.0.1:$P1/team/proj-a.git
EOF
echo 'distributionUrl=https\://services.gradle.org/distributions/gradle-8.14.3-bin.zip' >| "$WS/proj-a/gradle/wrapper/gradle-wrapper.properties"
cat >| "$WS/proj-b/azure-pipelines.yml" <<'EOF'
variables:
  - name: JAVA_HOME
    value: 'C:\Java\jdk-17'
  - name: GRADLE_USER_HOME
    value: 'D:\ci-cache\gradle'
resources:
  repositories:
    - repository: core
      type: git
      name: other-proj/product-core
jobs:
  - job: build
    timeoutInMinutes: 120
EOF
echo 'server: home' >| "$T/extra/group/proj-c/.ci-tools.yml"
ssh-keygen -q -t rsa -b 2048 -N '' -C test -f "$T/ssh/ado_rsa"
ssh-keygen -q -t ed25519 -N '' -C test -f "$T/ssh/id_ed25519"
echo '不是金鑰，腳本不應讀取內容' >| "$T/ssh/id_nopub"

mkdir -p "$T/cfg"
cat >| "$T/cfg/servers.yml" <<EOF
servers:
  home:
    url: http://127.0.0.1:$P1
    collection: Coll
    ssh_key: $T/ssh/ado_rsa
    pat_env: FAKE_ADO_TOKEN
    agents:
      - name: agent-1
        gradle_user_home: 'D:\ci-cache\gradle'
        cached_gradle_versions: [7.5.1]
EOF
snapshot() { (cd "$T" && find ws extra ssh cfg -exec stat -f '%N %m %z' {} + | sort); }

echo "== 情境 1：--no-network"
BEFORE="$(snapshot)"
OUT="$(env -u FAKE_ADO_TOKEN bash "$DISC" --project-dir "$WS/proj-a" --servers "$T/cfg/servers.yml" \
        --search-root "$T/extra" --ssh-dir "$T/ssh" --no-network 2>&1)"; RC=$?
show "$OUT"
check "exit 0" '[ $RC -eq 0 ]'
check "列出 server 與 agent 快取版本" 'printf "%s\n" "$OUT" | grep -q "server home：http://127.0.0.1:$P1/Coll" && printf "%s\n" "$OUT" | grep -q "已快取 Gradle=7.5.1"'
check "權杖環境變數未設定" 'printf "%s\n" "$OUT" | grep -q "FAKE_ADO_TOKEN：未設定"'
check "讀出 Gradle wrapper 版本" 'printf "%s\n" "$OUT" | grep -q "Gradle wrapper 版本：8.14.3"'
check "找到兄弟專案的 pipeline 與可搬的路徑" 'printf "%s\n" "$OUT" | grep -q "proj-b/azure-pipelines.yml" && printf "%s\n" "$OUT" | grep -Fq "JAVA_HOME = '"'"'C:\\Java\\jdk-17'"'"'"'
check "列出 pipeline 引用的 ADO repo" 'printf "%s\n" "$OUT" | grep -q "引用的 ADO repo：other-proj/product-core"'
check "--search-root 深層找到 .ci-tools.yml" 'printf "%s\n" "$OUT" | grep -q "extra/group/proj-c/.ci-tools.yml"'
check "不列出本專案自己的設定為「其他專案」" '! printf "%s\n" "$OUT" | grep -q "^  $WS/proj-a/"'
check "RSA 金鑰標示可用" 'printf "%s\n" "$OUT" | grep -q "ado_rsa：RSA 2048 bits  ← ADO 內建 SSH 可用"'
check "ed25519 不標示可用" 'printf "%s\n" "$OUT" | grep -q "id_ed25519：ED25519 256 bits$"'
check "沒有 .pub 的私鑰只列名稱" 'printf "%s\n" "$OUT" | grep -q "id_nopub：型別未知"'
check "不讀私鑰內容" '! printf "%s\n" "$OUT" | grep -q "不是金鑰"'
check "網路項目全部略過" 'printf "%s\n" "$OUT" | grep -q "略過（--no-network）" && printf "%s\n" "$OUT" | grep -q "（--no-network，略過）"'
check "建議：已有 server 跳過安裝" 'printf "%s\n" "$OUT" | grep -q "已有 server 設定 → 跳過「0. 評估」"'
check "建議：Gradle 8.14.3 未快取，第一次取消 offline" 'printf "%s\n" "$OUT" | grep -q "Gradle 8.14.3 不在 servers.yml 的 cached_gradle_versions"'
check "沒有寫入或修改任何檔案" '[ "$BEFORE" = "$(snapshot)" ]'

echo "== 情境 2：連本機模擬伺服器（Gitea＋ADO）"
SECRET="tok-$$-do-not-print"
OUT="$(FAKE_ADO_TOKEN="$SECRET" bash "$DISC" --project-dir "$WS/proj-a" --servers "$T/cfg/servers.yml" --ssh-dir "$T/ssh" 2>&1)"; RC=$?
show "$OUT"
check "exit 0" '[ $RC -eq 0 ]'
check "以 /api/v1/version 判斷為 Gitea" 'printf "%s\n" "$OUT" | grep -q "127.0.0.1:${P1}：Gitea 1.21.0"'
check "權杖已設定（不顯示值）" 'printf "%s\n" "$OUT" | grep -q "FAKE_ADO_TOKEN：已設定"'
check "列出 ADO 專案" 'printf "%s\n" "$OUT" | grep -q "的專案（2 個）：project-a other-proj"'
check "列出 agent pools" 'printf "%s\n" "$OUT" | grep -q "agent pools：Default"'
check "建議：ADO 專案已存在" 'printf "%s\n" "$OUT" | grep -q "ADO 專案 project-a 已存在"'
check "輸出不含權杖值" '! printf "%s\n" "$OUT" | grep -q "$SECRET"'

printf 'gitea_url: http://127.0.0.1:%s/team/proj-a.git\nado_url: ssh://ado-host:22/Coll/project-a/_git/proj-a\n' "$P1" >| "$WS/proj-a/.ci-tools.yml"
OUT="$(FAKE_ADO_TOKEN="$SECRET" bash "$DISC" --project-dir "$WS/proj-a" --servers "$T/cfg/servers.yml" --ssh-dir "$T/ssh" 2>&1)"; RC=$?
check "舊版單層格式：從 ado_url 取出 ADO 專案並判斷已存在" '[ $RC -eq 0 ] && printf "%s\n" "$OUT" | grep -q "舊版單層格式" && printf "%s\n" "$OUT" | grep -q "ADO 專案 project-a 已存在"'

echo "== 情境 3：GitLab 判斷"
printf 'repos:\n  - name: x\n    gitea_url: http://127.0.0.1:%s/team/x.git\n' "$P2" >| "$WS/proj-a/.ci-tools.yml"
OUT="$(bash "$DISC" --project-dir "$WS/proj-a" --servers "$T/none.yml" --ssh-dir "$T/ssh" 2>&1)"
show "$(printf '%s\n' "$OUT" | sed -n '/git 平台/,/ADO 現況/p')"
check "/api/v1/version 不是 JSON、/api/v4/version 回 200 JSON → GitLab" 'printf "%s\n" "$OUT" | grep -q "127.0.0.1:${P2}：GitLab"'

echo "== 情境 4：連不上時優雅降級"
cat >| "$T/cfg/dead.yml" <<'EOF'
servers:
  dead:
    url: http://127.0.0.1:1
    collection: Coll
    pat_env: FAKE_ADO_TOKEN
EOF
printf 'repos:\n  - name: x\n    gitea_url: http://127.0.0.1:1/team/x.git\n  - name: y\n    gitea_url: ssh://git@127.0.0.1:1/team/y.git\n' >| "$WS/proj-a/.ci-tools.yml"
OUT="$(FAKE_ADO_TOKEN=x bash "$DISC" --project-dir "$WS/proj-a" --servers "$T/cfg/dead.yml" --ssh-dir "$T/ssh" 2>&1)"; RC=$?
show "$(printf '%s\n' "$OUT" | sed -n '/git 平台/,$p')"
check "exit 0" '[ $RC -eq 0 ]'
check "git 主機連不上 → 略過" 'printf "%s\n" "$OUT" | grep -q "127.0.0.1:1：連不上（略過）"'
check "ssh:// 網址轉成 https://<主機> 探測（不沿用 SSH 連接埠）" 'printf "%s\n" "$OUT" | grep -q "https://127.0.0.1：連不上（略過）"'
check "ADO 連不上 → 略過" 'printf "%s\n" "$OUT" | grep -q "server dead：連不上"'

echo "== 情境 5：什麼都沒有"
mkdir -p "$T/empty/lonely" "$T/emptyssh"
OUT="$(bash "$DISC" --project-dir "$T/empty/lonely" --servers "$T/none.yml" --ssh-dir "$T/emptyssh" --no-network 2>&1)"; RC=$?
check "exit 0 且建議從評估開始" '[ $RC -eq 0 ] && printf "%s\n" "$OUT" | grep -q "從「0. 評估」開始"'
OUT="$(bash "$DISC" --project-dir "$T/empty/lonely" --servers "$T/none.yml" --ssh-dir "$T/ssh" --no-network 2>&1)"
check "只有 RSA 金鑰 → 先問使用者" 'printf "%s\n" "$OUT" | grep -q "先問使用者是否已經有 ADO server"'
OUT="$(bash "$DISC" --project-dir "$T/empty/lonely" --servers "$T/none.yml" --search-root "$WS" --ssh-dir "$T/emptyssh" --no-network 2>&1)"
check "找到其他專案設定 → 建議整理 servers.yml、不要重裝" 'printf "%s\n" "$OUT" | grep -q "不要直接重裝"'

echo "== 情境 6：參數錯誤"
bash "$DISC" --bogus >/dev/null 2>&1; RC=$?
check "未知參數 → exit 1" '[ $RC -eq 1 ]'
bash "$DISC" --project-dir "$T/nope" >/dev/null 2>&1; RC=$?
check "專案目錄不存在 → exit 1" '[ $RC -eq 1 ]'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
