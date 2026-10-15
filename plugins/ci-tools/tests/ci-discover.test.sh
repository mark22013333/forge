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
# CI_DISCOVER 可指向別的版本（例如修改前的腳本），用來做正對照：新案例在舊版上應該失敗
DISC="${CI_DISCOVER:-$HERE/../scripts/ci-discover.sh}"
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

echo "== 情境 7：測試外部連線（連線字串、註解剝除、不印帳密、Spring context 測試類）"
JP="$T/jproj"
mkdir -p "$JP/.git" "$JP/src/main/resources" "$JP/src/test/resources" "$JP/src/test/java/a"
cat >| "$T/cfg/same.yml" <<'EOF'
servers:
  lab-ado:
    url: http://10.9.8.7:8080
    collection: Coll
    pat_env: FAKE_ADO_TOKEN
EOF
# 第 1 行是被 # 註解掉的舊設定；第 2 行與 ADO server 同機且帶帳密；h2:mem 是記憶體庫
cat >| "$JP/src/main/resources/jdbc-test.properties" <<'EOF'
# jdbc.url=jdbc:sqlserver://10.0.0.99:1433;databaseName=old
jdbc.url=jdbc:sqlserver://10.9.8.7:1433;databaseName=dbNAMEx;user=sa;password=pwSECRET1
local.url=jdbc:mariadb://localhost:3306/UnitTest
mem.url=jdbc:h2:mem:testdb;MODE=MySQL
; ini.url=jdbc:mysql://10.0.0.95:3306/semicolon
user.url=jdbc:mysql://bobUSER:pwSECRET2@192.0.2.10:3306/db
esc.url=jdbc\:mysql\://10.9.8.8\:3306/escaped
EOF
# 帳密含各種特殊字元：輸出不得出現帳號（dbuser、guest、usrQ*）或密碼（4321、gu)est、pwZ*）的任何片段
cat >| "$JP/src/test/resources/leak.properties" <<'EOF'
a=jdbc:mysql://dbuser:4321?x@h1.corp.invalid:3306/db
b=amqp://guest:gu)est@mq.corp.invalid
c=jdbc:mysql://usrQ1:pwZ@pwZ@h3.corp.invalid:3306/db
d=jdbc:mysql://usrQ2:pwZ:pwZ@h4.corp.invalid/db
e=jdbc:mysql://usrQ3:pwZ/pwZ@h5.corp.invalid/db
f=jdbc:mysql://usrQ4:pwZ#pwZ@h6.corp.invalid/db
g=jdbc:mysql://usrQ5:pwZ?pwZ@h7.corp.invalid/db
h=jdbc:mysql://usrQ6:pwZ pwZ@h8.corp.invalid/db
i=jdbc:mysql://usrQ7:pwZ"pwZ@h9.corp.invalid/db
j=jdbc:mysql://usrQ8:pwZ'pwZ@h10.corp.invalid/db
k=jdbc:oracle:thin:usrQ9/pwZ@pwZ@h11.corp.invalid:1521:sid
l=jdbc:oracle:thin:usrQ10/pwZ pwZ@h12.corp.invalid:1521:sid
m=jdbc:sqlserver://h13.corp.invalid:1433;user=usrQ11;password=pwZ@pwZ
n=mongodb://usrQ12:pwZ;pwZ@h14.corp.invalid:27017/db
o=jdbc:mysql://usrQ13:pwZ,pwZ@h15.corp.invalid/db
EOF
# 不在掃描範圍：main 底下、檔名不含 test
echo 'jdbc.url=jdbc:mysql://10.0.0.96:3306/prod' >| "$JP/src/main/resources/application.properties"
cat >| "$JP/src/test/resources/hibernate.cfg.xml" <<'EOF'
<hibernate-configuration>
<!--
<property name="connection.url">jdbc:mysql://10.0.0.98:3306/commented</property>
-->
<property name="connection.url">jdbc:mysql://db.example.test:3306/real</property>
</hibernate-configuration>
EOF
cat >| "$JP/src/test/java/a/LiveTest.java" <<'EOF'
package a;

import org.junit.Ignore;
import org.junit.runner.RunWith;

@RunWith(SpringJUnit4ClassRunner.class)
@ContextConfiguration(classes = AppConfig.class)
public class LiveTest {
    // String old = "jdbc:mysql://10.0.0.97:3306/x";
    /* String older = "jdbc:mysql://10.0.0.94:3306/x"; */
    String url = "jdbc:postgresql://127.0.0.1:5432/x";
    @Ignore
    public void skipped() {}
}
EOF
cat >| "$JP/src/test/java/a/IgnoredTest.java" <<'EOF'
package a;

@Ignore("連真實環境")
@ContextConfiguration(classes = AppConfig.class)
public class IgnoredTest {
}
EOF
cat >| "$JP/src/test/java/a/DisabledTest.java" <<'EOF'
package a;

@Disabled
@SpringBootTest
class DisabledTest {
}
EOF
# 只有 import、註解被註解掉 → 不會啟動 Spring context
cat >| "$JP/src/test/java/a/ImportOnlyTest.java" <<'EOF'
package a;

import org.springframework.test.context.junit4.SpringJUnit4ClassRunner;
//@RunWith(SpringJUnit4ClassRunner.class)
public class ImportOnlyTest {
}
EOF
SNAP_JP() { (cd "$T" && find jproj -exec stat -f '%N %m %z' {} + | sort); }
BEFORE="$(SNAP_JP)"
OUT="$(bash "$DISC" --project-dir "$JP" --servers "$T/cfg/same.yml" --ssh-dir "$T/emptyssh" --no-network 2>&1)"; RC=$?
show "$(printf '%s\n' "$OUT" | sed -n '/== 測試外部連線/,/== 跨 repo/p')"
check "exit 0" '[ $RC -eq 0 ]'
check "與 servers.yml 主機同機的 jdbc → 標「同機」（含檔案:行號）" 'printf "%s\n" "$OUT" | grep -q "src/main/resources/jdbc-test.properties:2  jdbc:sqlserver://10.9.8.7:1433  ← ⚠️ 與 ADO server 同機"'
check "localhost → 標「agent 本機」" 'printf "%s\n" "$OUT" | grep -q "jdbc:mariadb://localhost:3306  ← CI 上會連到 agent 本機"'
check "Java 字串裡的 127.0.0.1 → 標「agent 本機」" 'printf "%s\n" "$OUT" | grep -q "LiveTest.java:11  jdbc:postgresql://127.0.0.1:5432  ← CI 上會連到 agent 本機"'
check "XML 註解外的設定照列" 'printf "%s\n" "$OUT" | grep -q "hibernate.cfg.xml:5  jdbc:mysql://db.example.test:3306"'
check "h2:mem → 不列" '! printf "%s\n" "$OUT" | grep -q "h2"'
check "被 # 註解的 jdbc 行 → 不列" '! printf "%s\n" "$OUT" | grep -q "10.0.0.99"'
check "被 ; 註解的 jdbc 行 → 不列" '! printf "%s\n" "$OUT" | grep -q "10.0.0.95"'
check "XML 註解區塊內 → 不列" '! printf "%s\n" "$OUT" | grep -q "10.0.0.98"'
check "Java // 與 /* */ 註解內 → 不列" '! printf "%s\n" "$OUT" | grep -q "10.0.0.97" && ! printf "%s\n" "$OUT" | grep -q "10.0.0.94"'
check "main 底下檔名不含 test 的設定檔 → 不在掃描範圍" '! printf "%s\n" "$OUT" | grep -q "10.0.0.96"'
check "帶帳密的連線字串只列主機與埠" 'printf "%s\n" "$OUT" | grep -q "jdbc:mysql://192.0.2.10:3306  ← 外部主機"'
check "輸出不含密碼、帳號與其餘參數" '! printf "%s\n" "$OUT" | grep -Eq "pwSECRET|bobUSER|dbNAMEx|password|user="'
check "帳密含 ? ) : / # @ 空白 引號 , ; 時，輸出不含帳號或密碼的任何片段" '! printf "%s\n" "$OUT" | grep -Eq "dbuser|4321|guest|gu\)|usrQ|pwZ"'
check "leak.properties 15 行全部列出（沒有因為特殊字元而整行消失）" '[ "$(printf "%s\n" "$OUT" | grep -c "leak.properties:")" -eq 15 ]'
check "無法確定帳密邊界時印「<無法解析>」" 'printf "%s\n" "$OUT" | grep -q "leak.properties:1  jdbc:mysql://<無法解析>" && printf "%s\n" "$OUT" | grep -q "leak.properties:2  amqp://<無法解析>" && printf "%s\n" "$OUT" | grep -q "leak.properties:5  jdbc:mysql://<無法解析>"'
check "密碼含 @、: 時以最後一個 @ 切掉帳密" 'printf "%s\n" "$OUT" | grep -q "leak.properties:3  jdbc:mysql://h3.corp.invalid:3306" && printf "%s\n" "$OUT" | grep -q "leak.properties:4  jdbc:mysql://h4.corp.invalid  "'
check "oracle 密碼含空白仍取到主機" 'printf "%s\n" "$OUT" | grep -q "leak.properties:12  jdbc:oracle:thin:@h12.corp.invalid:1521"'
check "Java .properties 跳脫寫法（\\: ）也抓得到" 'printf "%s\n" "$OUT" | grep -q "jdbc-test.properties:7  jdbc:mysql://10.9.8.8:3306  ← 外部主機"'
check "@ContextConfiguration 且類別上無 @Ignore → 列出" 'printf "%s\n" "$OUT" | grep -q "src/test/java/a/LiveTest.java:6  LiveTest  ← 會啟動 Spring context"'
check "類別上有 @Ignore → 不列" '! printf "%s\n" "$OUT" | grep -q "IgnoredTest"'
check "類別上有 @Disabled → 不列" '! printf "%s\n" "$OUT" | grep -q "DisabledTest"'
check "只有 import、Spring 註解被註解掉 → 不列" '! printf "%s\n" "$OUT" | grep -q "ImportOnlyTest"'
check "段末建議先只建置" 'printf "%s\n" "$OUT" | grep -q "建議：第一次建 CI 建議先只建置（-DskipTests／gradle assemble）"'
check "沒有寫入或修改任何檔案" '[ "$BEFORE" = "$(SNAP_JP)" ]'
OUT="$(bash "$DISC" --project-dir "$JP" --servers "$T/none.yml" --ssh-dir "$T/emptyssh" --no-network 2>&1)"
check "沒有 servers.yml 時同一個主機不標「同機」" 'printf "%s\n" "$OUT" | grep -q "jdbc:sqlserver://10.9.8.7:1433  ← 外部主機" && ! printf "%s\n" "$OUT" | grep -q "同機"'

echo "== 情境 8：跨 repo 相對引用"
FP="$T/frontrepo"
mkdir -p "$FP/.git" "$FP/web/src/components/Doc" "$FP/web/src/util" "$FP/web/node_modules/pkg"
cat >| "$FP/web/src/components/Doc/Doc.stories.js" <<'EOF'
import React from "react";
import "../../../../../Other/src";
import helper from "../../util";
// import "../../../../../Commented/src";
EOF
echo '@import "../../../../../Shared/vars";' >| "$FP/web/src/components/Doc/doc.scss"
echo 'require("../../../../Leak/x");' >| "$FP/web/node_modules/pkg/index.js"
OUT="$(bash "$DISC" --project-dir "$FP" --servers "$T/none.yml" --ssh-dir "$T/emptyssh" --no-network 2>&1)"; RC=$?
show "$(printf '%s\n' "$OUT" | sed -n '/== 跨 repo/,/== 其他專案/p')"
check "exit 0" '[ $RC -eq 0 ]'
check "stories 檔 import 跳出 repo 根 → 列出（檔案:行號＋解析後目標）" 'printf "%s\n" "$OUT" | grep -Fq "web/src/components/Doc/Doc.stories.js:2  \"../../../../../Other/src\" → $T/Other/src（repo 外的 Other）"'
check "scss @import 跳出 repo 根 → 列出" 'printf "%s\n" "$OUT" | grep -q "doc.scss:1  \"../../../../../Shared/vars\""'
check "未跳出 repo 根 → 不列" '! printf "%s\n" "$OUT" | grep -q "\"../../util\""'
check "註解掉的 import → 不列" '! printf "%s\n" "$OUT" | grep -q "Commented"'
check "node_modules 不掃" '! printf "%s\n" "$OUT" | grep -q "Leak"'
check "提示並排 checkout" 'printf "%s\n" "$OUT" | grep -q "CI 需並排 checkout 該 repo（resources.repositories）"'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
