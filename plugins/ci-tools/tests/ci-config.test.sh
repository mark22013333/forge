#!/usr/bin/env bash
# scripts/lib/ci_config.py 的離線測試：兩層設定檔的解析與輸出格式。
# 執行：bash plugins/ci-tools/tests/ci-config.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034,SC2001
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
CFG="$HERE/../scripts/lib/ci_config.py"
REF="$HERE/../skills/ado-ci-onboard/references"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
US=$'\x1f'

echo "== 情境 1：範例檔可解析"
OUT="$(python3 "$CFG" servers "$REF/servers.example.yml")"; RC=$?
check "servers.example.yml exit 0" '[ $RC -eq 0 ]'
check "server 名稱、collection、pat_env" '[ "$(printf "%s" "$OUT" | cut -d "$US" -f1,3,4)" = "home-ado${US}Collection${US}ADO_PAT" ]'
OUT="$(python3 "$CFG" agents "$REF/servers.example.yml" home-ado)"
check "agent 的已快取 Gradle 版本" '[ "$(printf "%s" "$OUT" | cut -d "$US" -f4)" = "7.5.1,8.14.3" ]'
OUT="$(python3 "$CFG" repos "$REF/ci-tools.example.yml" "$REF/servers.example.yml")"; RC=$?
check "repos exit 0 且兩筆" '[ $RC -eq 0 ] && [ "$(printf "%s\n" "$OUT" | wc -l | tr -d " ")" = 2 ]'
check "ado_url 由 ssh_url 樣式推出" 'printf "%s" "$OUT" | grep -Fq "ssh://ado-host:22/Collection/project-a/_git/product-core"'
check "get 取清單" '[ "$(python3 "$CFG" get "$REF/ci-tools.example.yml" repos.0.branches)" = "main,uat" ]'
check "get 取含冒號的引號字串" '[ "$(python3 "$CFG" get "$REF/ci-tools.example.yml" gradle_tasks)" = ":test :product-core:test" ]'

echo "== 情境 2：引號、註解、行內清單、空欄位"
cat >| "$T/a.yml" <<'EOF'
# 開頭註解
server: s1   # 行尾註解
quoted: "x # 不是註解"
single: 'it''s'
flow: [a, 'b c', "d"]
repos:
- name: r1
  gitea_url: file:///g/r1.git
  ado_url: file:///a/r1.git
- name: r2
  role: dependency
  gitea_url: file:///g/r2.git
  ado_url: file:///a/r2.git
  branches: [main]
EOF
check "雙引號內的 # 保留" '[ "$(python3 "$CFG" get "$T/a.yml" quoted)" = "x # 不是註解" ]'
check "單引號跳脫" '[ "$(python3 "$CFG" get "$T/a.yml" single)" = "it'"'"'s" ]'
check "行尾註解去除" '[ "$(python3 "$CFG" get "$T/a.yml" server)" = "s1" ]'
check "行內清單" '[ "$(python3 "$CFG" get "$T/a.yml" flow)" = "a,b c,d" ]'
OUT="$(python3 "$CFG" repos "$T/a.yml")"
IFS="$US" read -r n role g a br key <<<"$(printf "%s\n" "$OUT" | head -n1)"
check "空的 branches 欄位不會讓後面欄位錯位" '[ "$n" = r1 ] && [ "$role" = main ] && [ "$a" = file:///a/r1.git ] && [ -z "$br" ] && [ -z "$key" ]'
check "role 省略時為 main、第二筆為 dependency" 'printf "%s\n" "$OUT" | sed -n 2p | grep -q "^r2${US}dependency${US}"'
check "不存在的 key → exit 3" 'python3 "$CFG" get "$T/a.yml" nope >/dev/null; [ $? -eq 3 ]'

echo "== 情境 3：預設值"
cat >| "$T/s.yml" <<'EOF'
default_server: s2
servers:
  s2:
    url: http://h
    collection: C
    ssh_url: ssh://h/{collection}/{project}/_git/{repo}
EOF
printf 'ado_project: P\nrepos:\n  - name: app\n    gitea_url: g\n' >| "$T/p.yml"
OUT="$(python3 "$CFG" servers "$T/s.yml")"
check "pat_env 省略時為 ADO_PAT、api_version 預設 6.0" '[ "$(printf "%s" "$OUT" | cut -d "$US" -f4,7)" = "ADO_PAT${US}6.0" ]'
check "server 省略時用 default_server" '[ "$(python3 "$CFG" project-server "$T/p.yml" "$T/s.yml" | cut -d "$US" -f1)" = s2 ]'
check "repos 用 default_server 推出 ado_url" 'python3 "$CFG" repos "$T/p.yml" "$T/s.yml" | grep -Fq "ssh://h/C/P/_git/app"'
printf 'server: nope\n' >| "$T/p2.yml"
check "引用不存在的 server → exit 1" 'python3 "$CFG" project-server "$T/p2.yml" "$T/s.yml" 2>/dev/null; [ $? -eq 1 ]'

echo "== 情境 4：不支援的語法要報錯，不猜"
printf 'a: |\n  多行\n' >| "$T/bad1.yml"
check "多行字串 → exit 1 且有說明" 'python3 "$CFG" get "$T/bad1.yml" a 2>"$T/err1"; [ $? -eq 1 ] && grep -q "不支援" "$T/err1"'
printf 'a:\n\tb: 1\n' >| "$T/bad2.yml"
check "TAB 縮排 → exit 1" 'python3 "$CFG" get "$T/bad2.yml" a 2>/dev/null; [ $? -eq 1 ]'
printf 'a: {b: 1}\n' >| "$T/bad3.yml"
check "行內 mapping → exit 1" 'python3 "$CFG" get "$T/bad3.yml" a 2>/dev/null; [ $? -eq 1 ]'
check "檔案不存在 → exit 1" 'python3 "$CFG" get "$T/none.yml" a 2>/dev/null; [ $? -eq 1 ]'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
