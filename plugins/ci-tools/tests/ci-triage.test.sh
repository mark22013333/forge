#!/usr/bin/env bash
# ci-triage.sh 的離線測試：用假的建置 log 與本機 git repo，不連任何網路。
# 執行：bash plugins/ci-tools/tests/ci-triage.test.sh
# 暫存目錄由 mktemp 建立，測試結束後不清理（路徑會印出來）。
# check 以 eval 執行單引號字串，變數要延後展開，且只在 eval 內使用，shellcheck 看不到。
# shellcheck disable=SC2016,SC2034,SC2001
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
TRIAGE="$HERE/../scripts/ci-triage.sh"
T="$(mktemp -d)"
echo "暫存目錄：$T"

PASS=0
FAIL=0
ok()   { PASS=$((PASS + 1)); echo "  通過：$1"; }
bad()  { FAIL=$((FAIL + 1)); echo "  失敗：$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.com GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

# 假 repo：測試檔分散在兩個模組；DupTest 在兩個模組各有一個（無法唯一補全）
R="$T/repo"
git init -q -b main "$R"
mk() { mkdir -p "$R/$(dirname "$1")"; echo "class $(basename "$1" .java) {}" >| "$R/$1"; }
mk src/test/java/com/example/foo/FooServiceTest.java
mk src/test/java/com/example/OuterTest.java
mk src/test/java/com/example/baz/BazTest.java
mk core/src/test/java/com/example/a/DupTest.java
mk src/test/java/com/example/b/DupTest.java
git -C "$R" add -A && git -C "$R" commit -qm base
git -C "$R" tag product-base
echo "// 本專案的修改" >> "$R/src/test/java/com/example/foo/FooServiceTest.java"
git -C "$R" commit -qam "改了 FooServiceTest"
cat >| "$R/.ci-tools.yml" <<'EOF'
ado_project: proj-x
gradle_tasks: ':test :core:test'
baseline_branch: product-base
EOF

# ADO 下載的 log：每行有時間戳；Gradle 本機輸出：沒有時間戳、第一行帶 BOM
cat >| "$T/ado.log" <<'EOF'
2026-01-01T00:00:00.0000000Z > Task :test
2026-01-01T00:00:01.0000000Z FooServiceTest > savesThing() FAILED
2026-01-01T00:00:01.1000000Z     java.lang.AssertionError at FooServiceTest.java:10
2026-01-01T00:00:02.0000000Z FooServiceTest > deletesThing() FAILED
2026-01-01T00:00:03.0000000Z com.example.bar.BarTest > works FAILED
2026-01-01T00:00:04.0000000Z OuterTest > InnerCase > method() FAILED
2026-01-01T00:00:05.0000000Z DupTest > x() FAILED
2026-01-01T00:00:06.0000000Z MissingTest > y() FAILED
2026-01-01T00:00:06.5000000Z FooServiceTest > savesThing() PASSED
2026-01-01T00:00:07.0000000Z 10 tests completed, 6 failed, 1 skipped
EOF
printf '\xef\xbb\xbfBazTest > z() FAILED\n' >| "$T/local.log"

echo "== 情境 1：萃取與補全類別名（--log-file，不連網路）"
OUT="$(cd "$R" && bash "$TRIAGE" --log-file "$T/ado.log" --log-file "$T/local.log" --out "$T/out1" 2>&1)"; RC=$?
echo "$OUT" | sed 's/^/    | /'
F="$T/out1/failed-classes.txt"
check "exit 0" '[ $RC -eq 0 ]'
check "失敗類別 6 個（Foo、Bar、Outer、Dup、Missing、Baz）" '[ "$(wc -l < "$F" | tr -d " ")" = 6 ]'
check "簡短類別名補成完整類別名" 'grep -qx com.example.foo.FooServiceTest "$F" && grep -qx com.example.baz.BazTest "$F"'
check "已是完整類別名的照用" 'grep -qx com.example.bar.BarTest "$F"'
check "巢狀類別取最外層" 'grep -qx com.example.OuterTest "$F" && ! grep -q InnerCase "$F"'
check "第一行 BOM 不影響萃取" 'grep -qx com.example.baz.BazTest "$F"'
check "FooServiceTest 失敗 2 個方法、PASSED 不算" 'grep -q "^com.example.foo.FooServiceTest	2	" "$T/out1/failed-tests.tsv"'
check "找到多個的列入 unresolved" 'grep -q "^DupTest	找到多個" "$T/out1/unresolved.txt"'
check "找不到原始檔的列入 unresolved" 'grep -q "^MissingTest	找不到原始檔" "$T/out1/unresolved.txt"'
check "印出 Gradle 摘要" 'echo "$OUT" | grep -q "10 tests completed, 6 failed, 1 skipped"'
check "預設讀目前目錄的 .ci-tools.yml：重跑指令用 gradle_tasks" 'grep -q "RECENT_TESTS_FILE=.* ./gradlew :test :core:test --continue --init-script ci/ci-init.gradle" "$T/out1/commands.txt"'
check "基準比對指令用 baseline_branch" 'grep -q "^git diff --stat product-base HEAD --" "$T/out1/commands.txt"'

echo "== 情境 2：產生的基準比對指令實際可用"
CMD="$(grep '^git diff' "$T/out1/commands.txt")"
DIFF="$(cd "$R" && eval "$CMD")"
echo "$DIFF" | sed 's/^/    | /'
check "本專案改過的 FooServiceTest 出現在比對結果" 'echo "$DIFF" | grep -q "FooServiceTest.java"'
check "沒改過的 BazTest 不出現" '! echo "$DIFF" | grep -q "BazTest"'

echo "== 情境 3：參數覆寫與沒有失敗的 log"
OUT="$(bash "$TRIAGE" --log-file "$T/ado.log" --repo-dir "$R" --gradle-tasks test --baseline v1.0 --out "$T/out2" 2>&1)"; RC=$?
check "覆寫 Gradle task 與基準" '[ $RC -eq 0 ] && grep -q "./gradlew test --continue" "$T/out2/commands.txt" && grep -q "diff --stat v1.0 HEAD" "$T/out2/commands.txt"'
printf '2026-01-01T00:00:00.0Z BUILD SUCCESSFUL\n' >| "$T/green.log"
OUT="$(bash "$TRIAGE" --log-file "$T/green.log" --repo-dir "$R" --out "$T/out3" 2>&1)"; RC=$?
check "沒有失敗 → exit 0 並說明" '[ $RC -eq 0 ] && echo "$OUT" | grep -q "沒有找到「<類別> > <方法> FAILED」" && [ ! -s "$T/out3/failed-classes.txt" ]'

echo "== 情境 4：參數錯誤"
bash "$TRIAGE" >/dev/null 2>&1; RC=$?
check "沒有 --build 也沒有 --log-file → exit 1" '[ $RC -eq 1 ]'
bash "$TRIAGE" --log-file "$T/none.log" --out "$T/out4" >/dev/null 2>&1; RC=$?
check "log 檔不存在 → exit 1" '[ $RC -eq 1 ]'
OUT="$(cd "$T" && bash "$TRIAGE" --build 1 --out "$T/out5" 2>&1)"; RC=$?
check "要連 ADO 卻沒有 .ci-tools.yml → exit 1，不發請求" '[ $RC -eq 1 ] && echo "$OUT" | grep -q "需要 --config"'

echo
echo "結果：通過 ${PASS}、失敗 ${FAIL}"
[ "$FAIL" -eq 0 ]
