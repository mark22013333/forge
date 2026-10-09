#!/usr/bin/env bash
# sync-all-repos.sh — 依 .ci-tools.yml 的 repos 清單，逐一呼叫 sync-gitea-to-ado.sh
#
# 一個專案可能有多個 repo（例：主 repo 的 settings.gradle 引用同層的核心 repo），
# 每個都要同步到 ADO，否則 CI 會拿到舊的依賴（踩坑 14）。
#
# 為什麼另寫一支包裝腳本，而不是在 sync-gitea-to-ado.sh 加 --config：
#   單 repo 腳本已有測試守著它的參數與結束碼，包裝腳本只負責「讀設定、排順序、整批把關」，
#   單 repo 的行為一行都不用動，既有用法保證不變。
#
# 規則：
#   - 預設只預覽。加 --apply 才推送。
#   - --apply 時先把所有 repo 預覽一遍：任一 repo 有分叉或錯誤，整批（所有 repo）都不推。
#   - 推送順序：role: dependency 先、role: main 後，避免主 repo 先觸發 CI 卻拿到舊的依賴。
#   - 保護分支、只快轉、不強制推送等規則都沿用 sync-gitea-to-ado.sh。
#
# 用法：
#   sync-all-repos.sh --config <.ci-tools.yml> [選項]
#
# 選項：
#   --config <path>        專案層 .ci-tools.yml（必填）
#   --servers <path>       使用者層 servers.yml（預設：${CI_TOOLS_SERVERS}，
#                          否則 ${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml）
#   --only <a,b>           只處理這些 repo（以 repos[].name 指定）
#   --workdir-root <dir>   各 repo 的工作 clone 放在 <dir>/<name>.git（預設沿用單 repo 腳本的預設）
#   --apply                實際推送（未帶時只預覽）
#   -h, --help             顯示說明
#
# 結束碼：0 正常；1 參數、設定或執行錯誤；2 有分叉，--apply 未推任何 repo

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

HERE="$(cd "$(dirname "$0")" && pwd)"
SYNC="$HERE/sync-gitea-to-ado.sh"
CFG="$HERE/lib/ci_config.py"

CONFIG=""
SERVERS="${CI_TOOLS_SERVERS:-${XDG_CONFIG_HOME:-$HOME/.config}/ci-tools/servers.yml}"
ONLY=""
WORKDIR_ROOT=""
APPLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --config) CONFIG="${2:-}"; shift 2 ;;
    --servers) SERVERS="${2:-}"; shift 2 ;;
    --only) ONLY="${2:-}"; shift 2 ;;
    --workdir-root) WORKDIR_ROOT="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

[ -n "$CONFIG" ] || { echo "錯誤：--config 為必填" >&2; exit 1; }
[ -f "$CONFIG" ] || { echo "錯誤：找不到 ${CONFIG}" >&2; exit 1; }

ROWS="$(python3 "$CFG" repos "$CONFIG" "$SERVERS")" || exit 1
PROTECTED="$(python3 "$CFG" get "$CONFIG" protected_branches 2>/dev/null || true)"

# 排序：dependency 先、main 後（同 role 內維持設定檔順序）
ORDERED="$(printf '%s\n' "$ROWS" | awk -F'\037' 'NF && $2 == "dependency"'; printf '%s\n' "$ROWS" | awk -F'\037' 'NF && $2 != "dependency"')"
if [ -n "$ONLY" ]; then
  ORDERED="$(printf '%s\n' "$ORDERED" | awk -F'\037' -v only=",$ONLY," 'index(only, "," $1 ",")')"
fi
[ -n "$ORDERED" ] || { echo "錯誤：沒有要處理的 repo（檢查 repos 清單或 --only）" >&2; exit 1; }

# 組出單 repo 腳本的參數（以陣列傳遞，路徑含空白也不會被拆開）
run_one() {
  local name="$1" gitea="$2" ado="$3" branches="$4" key="$5" mode="$6"
  local args=(--gitea "$gitea" --ado "$ado")
  [ -n "$branches" ] && args+=(--branches "$branches")
  [ -n "$key" ] && args+=(--key "$key")
  [ -n "$PROTECTED" ] && args+=(--protected "$PROTECTED")
  [ -n "$WORKDIR_ROOT" ] && args+=(--workdir "$WORKDIR_ROOT/${name}.git")
  [ "$mode" = apply ] && args+=(--apply)
  bash "$SYNC" "${args[@]}"
}

check_row() {
  local name="$1" gitea="$2" ado="$3"
  if [ -z "$gitea" ] || [ -z "$ado" ]; then
    echo "錯誤：repo $name 缺 gitea_url 或 ado_url（ado_url 可由 servers.yml 的 ssh_url 樣式推出）" >&2
    return 1
  fi
}

# ---------------------------------------------------------------- 第一輪：全部預覽
RC=0
BLOCKED=""
while IFS=$'\x1f' read -r name role gitea ado branches key; do
  [ -n "$name" ] || continue
  echo "### ${name}（${role}）"
  check_row "$name" "$gitea" "$ado" || { RC=1; BLOCKED="$BLOCKED $name"; continue; }
  if out="$(run_one "$name" "$gitea" "$ado" "$branches" "$key" preview 2>&1)"; then rc=0; else rc=$?; fi
  printf '%s\n' "$out"
  if [ "$rc" -ne 0 ]; then
    RC=1; BLOCKED="$BLOCKED $name"
  # 限制：以單 repo 腳本預覽輸出的「已分叉 (」字樣判斷，兩支腳本的文字綁在一起；
  # 且預覽與實際推送之間若有人推了新 commit，可能前面的 repo 已推、後面的才分叉（每個 repo 推送時仍只快轉）。
  elif printf '%s\n' "$out" | grep -q ' 已分叉 ('; then
    [ "$RC" -eq 0 ] && RC=2
    BLOCKED="$BLOCKED $name"
  fi
  echo
done <<<"$ORDERED"

if [ "$APPLY" -eq 0 ]; then
  echo "（預覽模式，未推送任何 repo。確認無誤後加 --apply。）"
  [ "$RC" -eq 2 ] && RC=0   # 預覽時分叉只是狀態，不算錯誤（與單 repo 腳本一致）
  exit "$RC"
fi

if [ -n "$BLOCKED" ]; then
  echo "下列 repo 有分叉或錯誤，整批（所有 repo）都不推送：$BLOCKED" >&2
  echo "請先處理，或在 .ci-tools.yml 調整 branches、用 --only 排除後再執行。" >&2
  exit "$RC"
fi

# ---------------------------------------------------------------- 第二輪：依序推送
while IFS=$'\x1f' read -r name role gitea ado branches key; do
  [ -n "$name" ] || continue
  echo "### 推送 ${name}（${role}）"
  if ! run_one "$name" "$gitea" "$ado" "$branches" "$key" apply; then
    echo "錯誤：repo $name 推送失敗，後續 repo 不再處理" >&2
    exit 1
  fi
  echo
done <<<"$ORDERED"
exit 0
