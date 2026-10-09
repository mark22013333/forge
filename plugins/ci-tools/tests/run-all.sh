#!/usr/bin/env bash
# 依序執行 ci-tools 的全部離線測試，任一失敗則 exit 1。
# 執行：bash plugins/ci-tools/tests/run-all.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
FAILED=""
for t in sync-gitea-to-ado ci-config sync-all-repos ci-triage ci-discover; do
  echo "########## ${t}.test.sh"
  if ! bash "$HERE/${t}.test.sh"; then FAILED="$FAILED $t"; fi
  echo
done

if [ -n "$FAILED" ]; then
  echo "有測試檔失敗：$FAILED"
  exit 1
fi
echo "全部測試檔通過"
