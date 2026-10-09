#!/usr/bin/env bash
# sync-gitea-to-ado.sh 的離線測試：用兩個本機 bare repo 模擬 Gitea 與 ADO，不連任何真實主機。
# 執行：bash plugins/ci-tools/tests/sync-gitea-to-ado.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開；BEFORE／RC 由 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034,SC2001
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SYNC="$HERE/../scripts/sync-gitea-to-ado.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

G="$T/gitea.git"
A="$T/ado.git"
W="$T/work"
git init -q --bare "$G"
git init -q --bare "$A"
git init -q -b base "$W"

c() { echo "$1" >>"$W/f"; git -C "$W" add f; git -C "$W" commit -qm "$1"; }

c base1
BASE=$(git -C "$W" rev-parse HEAD)

# same：兩邊一致
git -C "$W" push -q "$G" "$BASE:refs/heads/same"
git -C "$W" push -q "$A" "$BASE:refs/heads/same"

# ff：Gitea 領先 2
git -C "$W" checkout -q -b ff "$BASE"; c ff1; c ff2
git -C "$W" push -q "$G" ff:refs/heads/ff
git -C "$W" push -q "$A" "$BASE:refs/heads/ff"

# adoahead：ADO 領先 1
git -C "$W" checkout -q -b adoahead "$BASE"; c aa1
git -C "$W" push -q "$G" "$BASE:refs/heads/adoahead"
git -C "$W" push -q "$A" adoahead:refs/heads/adoahead

# diverged：兩邊各有 1 個不同 commit
git -C "$W" checkout -q -b dg "$BASE"; c dgG
git -C "$W" push -q "$G" dg:refs/heads/diverged
git -C "$W" checkout -q -b da "$BASE"; c dgA
git -C "$W" push -q "$A" da:refs/heads/diverged

# newb：只在 Gitea
git -C "$W" checkout -q -b newb "$BASE"; c nb1
git -C "$W" push -q "$G" newb:refs/heads/newb

# adoonly：只在 ADO
git -C "$W" push -q "$A" "$BASE:refs/heads/adoonly"

# main：保護分支，Gitea 領先 1
git -C "$W" checkout -q -b m "$BASE"; c m1
git -C "$W" push -q "$G" m:refs/heads/main
git -C "$W" push -q "$A" "$BASE:refs/heads/main"

ado_refs() { git ls-remote "$A" | sort; }
gitea_sha() { git ls-remote "$G" "refs/heads/$1" | cut -f1; }
ado_sha() { git ls-remote "$A" "refs/heads/$1" | cut -f1; }

GU="file://$G"
AU="file://$A"
WD="$T/mirror.git"

echo "== 情境 1：預覽（不帶 --apply）"
BEFORE="$(ado_refs)"
OUT="$(bash "$SYNC" --gitea "$GU" --ado "$AU" --workdir "$WD" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "預覽 exit 0" '[ $RC -eq 0 ]'
check "same＝一致" 'echo "$OUT" | grep -Eq "^same +一致$"'
check "ff＝可快轉 (領先 2)" 'echo "$OUT" | grep -Eq "^ff +可快轉 \(領先 2\)$"'
check "adoahead＝ADO 領先 (1)" 'echo "$OUT" | grep -Eq "^adoahead +ADO 領先 \(1\)$"'
check "diverged＝已分叉" 'echo "$OUT" | grep -Eq "^diverged +已分叉"'
check "newb＝新分支" 'echo "$OUT" | grep -Eq "^newb +新分支$"'
check "adoonly＝僅 ADO" 'echo "$OUT" | grep -Eq "^adoonly +僅 ADO$"'
check "main＝可快轉 (領先 1)" 'echo "$OUT" | grep -Eq "^main +可快轉 \(領先 1\)$"'
check "預覽未改動 ADO" '[ "$BEFORE" = "$(ado_refs)" ]'

echo "== 情境 2：--apply 且有分叉 → 整批不推"
OUT="$(bash "$SYNC" --gitea "$GU" --ado "$AU" --workdir "$WD" --apply 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 非 0" '[ $RC -ne 0 ]'
check "輸出點名 diverged" 'echo "$OUT" | grep -q "diverged"'
check "ADO 完全未改動" '[ "$BEFORE" = "$(ado_refs)" ]'

echo "== 情境 3：--apply 排除分叉分支"
OUT="$(bash "$SYNC" --gitea "$GU" --ado "$AU" --workdir "$WD" --branches same,ff,adoahead,newb,main --apply 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
check "exit 0" '[ $RC -eq 0 ]'
check "ff 已推到與 Gitea 相同" '[ "$(ado_sha ff)" = "$(gitea_sha ff)" ]'
check "newb 已建立且與 Gitea 相同" '[ -n "$(ado_sha newb)" ] && [ "$(ado_sha newb)" = "$(gitea_sha newb)" ]'
check "adoahead 未被改動" '[ "$(ado_sha adoahead)" = "$(git -C "$W" rev-parse adoahead)" ]'
check "main 未被推" '[ "$(ado_sha main)" = "$BASE" ]'
check "main 印出可貼上的推送指令" 'echo "$OUT" | grep -Eq "push ado $(gitea_sha main):refs/heads/main"'
check "推後驗證 ff 一致" 'echo "$OUT" | grep -Eq "驗證 ff .*一致"'
check "推後驗證 newb 一致" 'echo "$OUT" | grep -Eq "驗證 newb .*一致"'
check "指令文字中沒有 force 旗標" '! grep -Eq -- "--force|push.*[[:space:]]\+" "$SYNC"'

echo "== 情境 4：預設 workdir 為持久目錄"
OUT="$(XDG_DATA_HOME="$T/xdg" bash "$SYNC" --gitea "$GU" --ado "$AU" 2>&1)"; RC=$?
check "使用 XDG_DATA_HOME 下的 ci-tools/mirrors/gitea.git" 'echo "$OUT" | grep -Fq "$T/xdg/ci-tools/mirrors/gitea.git" && [ -f "$T/xdg/ci-tools/mirrors/gitea.git/HEAD" ]'
OUT="$(env -u XDG_DATA_HOME HOME="$T/home" bash "$SYNC" --gitea "$GU" --ado "$AU" 2>&1)"; RC=$?
check "無 XDG_DATA_HOME 時落在 \$HOME/.local/share" 'echo "$OUT" | grep -Fq "$T/home/.local/share/ci-tools/mirrors/gitea.git"'

echo "== 情境 5：工作 clone 失去 HEAD/config 仍可自癒（踩坑 16）"
mv "$WD/HEAD" "$T/HEAD.bak"; mv "$WD/config" "$T/config.bak"
OUT="$(bash "$SYNC" --gitea "$GU" --ado "$AU" --workdir "$WD" 2>&1)"; RC=$?
check "自癒後預覽 exit 0 且 same＝一致" '[ $RC -eq 0 ] && echo "$OUT" | grep -Eq "^same +一致$"'

echo "== 情境 6：參數錯誤"
bash "$SYNC" --ado "$AU" >/dev/null 2>&1; RC=$?
check "缺 --gitea 時 exit 非 0" '[ $RC -ne 0 ]'
OUT="$(bash "$SYNC" --gitea "$GU" --ado "$AU" --workdir "$WD" --branches nope 2>&1)"; RC=$?
check "指定不存在的分支 → 回報且 exit 非 0" '[ $RC -ne 0 ] && echo "$OUT" | grep -Eq "^nope +兩邊皆無"'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
