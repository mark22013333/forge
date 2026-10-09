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

echo "== 情境 6：單 repo 腳本沒有被改動"
check "sync-gitea-to-ado.sh 與 origin/master 相同" 'git -C "$HERE/.." diff --quiet origin/master -- scripts/sync-gitea-to-ado.sh 2>/dev/null || ! git -C "$HERE/.." rev-parse -q --verify origin/master >/dev/null'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
