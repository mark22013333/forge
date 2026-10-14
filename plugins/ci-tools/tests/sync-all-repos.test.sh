#!/usr/bin/env bash
# sync-all-repos.sh 的離線測試：用本機 bare repo 模擬兩個 repo（主 repo＋依賴 repo）的 Gitea 與 ADO。
# 執行：bash plugins/ci-tools/tests/sync-all-repos.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034,SC2001
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
ALL="$HERE/../scripts/sync-all-repos.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
show() { printf '%s\n' "$1" | sed 's/^/    | /'; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

# Gitea 端：app（主 repo）、core（依賴 repo）
GA="$T/gitea/app.git"; GC="$T/gitea/core.git"
# ADO 端：app 由 servers.yml 的 ssh_url 樣式推出（file://$T/ado/{project}/{repo}.git），core 用 ado_url 直接指定
AA="$T/ado/proj-x/app-repo.git"; AC="$T/ado-core.git"
for r in "$GA" "$GC" "$AA" "$AC"; do git init -q --bare "$r"; done
W="$T/work"; git init -q -b base "$W"
c() { echo "$1" >>"$W/f"; git -C "$W" add f; git -C "$W" commit -qm "$1"; }
c base1
BASE=$(git -C "$W" rev-parse HEAD)

git -C "$W" checkout -q -b app-main "$BASE"; c am1
git -C "$W" push -q "$GA" app-main:refs/heads/main
git -C "$W" checkout -q -b app-dev "$BASE"; c ad1
git -C "$W" push -q "$GA" app-dev:refs/heads/dev
git -C "$W" checkout -q -b core-rel "$BASE"; c cr1
git -C "$W" push -q "$GC" core-rel:refs/heads/rel

sha() { git ls-remote "$1" "refs/heads/$2" | cut -f1; }
ado_refs() { { git ls-remote "$AA"; git ls-remote "$AC"; } | sort; }

mkdir -p "$T/cfg"
cat >| "$T/cfg/servers.yml" <<EOF
servers:
  local:
    url: http://127.0.0.1:1
    collection: Coll
    ssh_url: file://$T/ado/{project}/{repo}.git
EOF
cat >| "$T/cfg/ci-tools.yml" <<EOF
server: local
ado_project: proj-x
repos:
  - name: app
    role: main
    gitea_url: file://$GA
    ado_repo: app-repo
    branches: [main, dev]
  - name: core
    role: dependency
    gitea_url: file://$GC
    ado_url: file://$AC
    branches: [rel]
protected_branches: [main]
EOF
RUN=(bash "$ALL" --config "$T/cfg/ci-tools.yml" --servers "$T/cfg/servers.yml" --workdir-root "$T/mirrors")

echo "== 情境 1：預覽"
BEFORE="$(ado_refs)"
OUT="$("${RUN[@]}" 2>&1)"; RC=$?
show "$OUT"
check "預覽 exit 0" '[ $RC -eq 0 ]'
check "依賴 repo 排在主 repo 前面" '[ "$(printf "%s\n" "$OUT" | grep -n "^### core" | cut -d: -f1)" -lt "$(printf "%s\n" "$OUT" | grep -n "^### app" | cut -d: -f1)" ]'
check "app 的 ADO 網址由樣式推出（新分支 dev）" 'printf "%s\n" "$OUT" | grep -Eq "^dev +新分支$"'
check "core 的 rel＝新分支" 'printf "%s\n" "$OUT" | grep -Eq "^rel +新分支$"'
check "工作 clone 放在 --workdir-root/<name>.git" '[ -f "$T/mirrors/app.git/HEAD" ] && [ -f "$T/mirrors/core.git/HEAD" ]'
check "預覽未改動 ADO" '[ "$BEFORE" = "$(ado_refs)" ]'

echo "== 情境 2：--apply 推送兩個 repo"
OUT="$("${RUN[@]}" --apply 2>&1)"; RC=$?
show "$OUT"
check "exit 0" '[ $RC -eq 0 ]'
check "推送順序：core 先、app 後" '[ "$(printf "%s\n" "$OUT" | grep -n "^### 推送 core" | cut -d: -f1)" -lt "$(printf "%s\n" "$OUT" | grep -n "^### 推送 app" | cut -d: -f1)" ]'
check "core rel 已推到與 Gitea 相同" '[ "$(sha "$AC" rel)" = "$(sha "$GC" rel)" ]'
check "app dev 已推到與 Gitea 相同" '[ "$(sha "$AA" dev)" = "$(sha "$GA" dev)" ]'
check "保護分支 main 沒有被推" '[ -z "$(sha "$AA" main)" ]'
check "保護分支 main 印出指令" 'printf "%s\n" "$OUT" | grep -Eq "push ado $(sha "$GA" main):refs/heads/main"'
PF="$T/mirrors/pending-protected-pushes.sh"
check "最後有彙整段，且在所有「### 推送」之後" 'sum=$(printf "%s\n" "$OUT" | grep -n "保護分支待推指令" | cut -d: -f1); last=$(printf "%s\n" "$OUT" | grep -n "^### 推送" | tail -1 | cut -d: -f1); [ -n "$sum" ] && [ "$sum" -gt "$last" ]'
check "彙整段標出 repo 名稱與指令" 'printf "%s\n" "$OUT" | grep -Eq "^\[app\] .*push ado $(sha "$GA" main):refs/heads/main"'
check "寫成 <root>/pending-protected-pushes.sh 並印出路徑" '[ -x "$PF" ] && printf "%s\n" "$OUT" | grep -Fq "已寫成：$PF"'
check "檔案開頭有「未執行」註解、內容只有 main 的 push" 'sed -n 2,5p "$PF" | grep -q "未執行" && [ "$(grep -c "push ado" "$PF")" -eq 1 ] && grep -Eq "push ado $(sha "$GA" main):refs/heads/main" "$PF"'
check "檔案語法正確（bash -n，不執行）" 'bash -n "$PF"'
check "檔頭有產生時間與涵蓋的 repo 清單" 'sed -n 2,5p "$PF" | grep -Eq "於 [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} 產生" && sed -n 2,5p "$PF" | grep -q "涵蓋 repo：core app"'

echo "== 情境 3：一個 repo 分叉 → 所有 repo 都不推"
git -C "$W" checkout -q core-rel; c cr-ado
git -C "$W" push -q "$AC" core-rel:refs/heads/rel           # 有人直接推到 ADO
git -C "$W" checkout -q -b core-rel2 "$(sha "$GC" rel)"; c cr-gitea
git -C "$W" push -q "$GC" core-rel2:refs/heads/rel          # Gitea 也前進 → 分叉
git -C "$W" checkout -q app-dev; c ad2
git -C "$W" push -q "$GA" app-dev:refs/heads/dev            # app 可快轉
BEFORE="$(ado_refs)"
OUT="$("${RUN[@]}" --apply 2>&1)"; RC=$?
show "$OUT"
check "exit 2" '[ $RC -eq 2 ]'
check "點名分叉的 repo" 'printf "%s\n" "$OUT" | grep -q "整批.*core"'
check "可快轉的 app dev 也沒被推（整批把關）" '[ "$BEFORE" = "$(ado_refs)" ]'
OUT="$("${RUN[@]}" 2>&1)"; RC=$?
check "同樣狀態下預覽 exit 0（分叉只是狀態）" '[ $RC -eq 0 ] && printf "%s\n" "$OUT" | grep -Eq "^rel +已分叉"'

echo "== 情境 4：--only 只處理指定 repo"
OUT="$("${RUN[@]}" --only app --apply 2>&1)"; RC=$?
show "$OUT"
check "exit 0 且沒有處理 core" '[ $RC -eq 0 ] && ! printf "%s\n" "$OUT" | grep -q "^### core"'
check "app dev 已推到最新" '[ "$(sha "$AA" dev)" = "$(sha "$GA" dev)" ]'

echo "== 情境 5：設定錯誤"
bash "$ALL" >/dev/null 2>&1; RC=$?
check "缺 --config → exit 1" '[ $RC -eq 1 ]'
printf 'repos:\n  - name: lonely\n    gitea_url: file:///x.git\n' >| "$T/cfg/no-ado.yml"
OUT="$(bash "$ALL" --config "$T/cfg/no-ado.yml" --servers "$T/none.yml" 2>&1)"; RC=$?
check "repo 推不出 ado_url → exit 1 並說明" '[ $RC -eq 1 ] && printf "%s\n" "$OUT" | grep -q "缺 gitea_url 或 ado_url"'
OUT="$("${RUN[@]}" --only nope 2>&1)"; RC=$?
check "--only 指定不存在的 repo → exit 1" '[ $RC -eq 1 ]'

echo "== 情境 6：wrapper 依賴的單 repo 腳本輸出契約仍成立"
# sync-all-repos.sh 以文字比對單 repo 腳本的輸出：' 已分叉 (' 判斷分叉、'  …git --git-dir=… push ado <sha>:refs/heads/' 擷取保護分支指令
ONE="$HERE/../scripts/sync-gitea-to-ado.sh"
check "單 repo 腳本原始碼仍有「已分叉 (」與「push ado …:refs/heads/」" 'grep -Fq "已分叉 (" "$ONE" && grep -Eq "push ado .*:refs/heads/" "$ONE"'
OUT="$(bash "$ONE" --gitea "file://$GC" --ado "file://$AC" --workdir "$T/contract-core.git" 2>&1)"
check "實跑分叉的 core：輸出含 wrapper 比對的「 已分叉 (」" 'printf "%s\n" "$OUT" | grep -q " 已分叉 ("'
OUT="$(bash "$ONE" --gitea "file://$GA" --ado "file://$AA" --workdir "$T/contract-app.git" --branches main --protected main --apply 2>&1)"
check "實跑保護分支：指令行符合 wrapper 的擷取格式" 'printf "%s\n" "$OUT" | grep -Eq "^  .*git --git-dir=.* push ado [0-9a-f]+:refs/heads/main$"'
check "保護分支仍未被推（契約實跑不改動 ADO）" '[ -z "$(sha "$AA" main)" ]'

echo "== 情境 7：未指定 --workdir-root 時，工作 clone 依 <server>/<ado_project> 分目錄"
X="$T/xdg"
# 先用單 repo 腳本的舊預設路徑（mirrors/<basename>.git）建一個遠端相同的 app 工作 clone
XDG_DATA_HOME="$X" bash "$ONE" --gitea "file://$GA" --ado "file://$AA" >/dev/null 2>&1
DEF=(bash "$ALL" --config "$T/cfg/ci-tools.yml" --servers "$T/cfg/servers.yml")
OUT="$(XDG_DATA_HOME="$X" "${DEF[@]}" --only app 2>&1)"; RC=$?
show "$OUT"
check "預覽 exit 0" '[ $RC -eq 0 ]'
check "工作 clone 落在 mirrors/local/proj-x/app.git" '[ -f "$X/ci-tools/mirrors/local/proj-x/app.git/HEAD" ] && printf "%s\n" "$OUT" | grep -Fq "工作 clone：$X/ci-tools/mirrors/local/proj-x/app.git"'
check "舊預設路徑遠端相同 → 印出改用新路徑的提示" 'printf "%s\n" "$OUT" | grep -Fq "提示：舊預設路徑 $X/ci-tools/mirrors/app.git"'
check "舊預設路徑原封不動（遠端仍指向 app）" '[ "$(git --git-dir="$X/ci-tools/mirrors/app.git" remote get-url gitea)" = "file://$GA" ]'

echo "== 情境 8：沒有保護分支待推 → 不產生檔案"
sed 's/branches: \[main, dev\]/branches: [dev]/' "$T/cfg/ci-tools.yml" >| "$T/cfg/no-main.yml"
OUT="$(bash "$ALL" --config "$T/cfg/no-main.yml" --servers "$T/cfg/servers.yml" --workdir-root "$T/mirrors-np" --only app --apply 2>&1)"; RC=$?
show "$OUT"
check "exit 0、沒有彙整段、沒有產生檔案" '[ $RC -eq 0 ] && ! printf "%s\n" "$OUT" | grep -q "保護分支待推指令" && [ ! -e "$T/mirrors-np/pending-protected-pushes.sh" ]'
OUT="$(bash "$ALL" --config "$T/cfg/no-main.yml" --servers "$T/cfg/servers.yml" --workdir-root "$T/mirrors" --only app --apply 2>&1)"; RC=$?
check "同目錄留有舊檔時提示其已過期" '[ $RC -eq 0 ] && printf "%s\n" "$OUT" | grep -Fq "$PF 是先前執行留下的"'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
