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
#   --workdir-root <dir>   各 repo 的工作 clone 放在 <dir>/<name>.git
#                          （預設：${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors/<server>/<ado_project>，
#                          依專案分目錄，避免不同專案的同名 repo 共用同一個工作 clone）
#   --apply                實際推送（未帶時只預覽）
#   -h, --help             顯示說明
#
# 保護分支待推指令：--apply 結束時彙整所有 repo 的保護分支指令印在最後，
#   並寫成 <workdir-root>/pending-protected-pushes.sh（腳本不會執行它；沒有待推時不產生）。
#   該檔每次產生都「整個覆寫」，只含該次處理的 repo（檔頭會列出）；用 --only 分次執行時，
#   後一次會蓋掉前一次的清單，請在每次執行後先處理完再跑下一批。
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

# 工作 clone 根目錄：未指定時依 <server>/<ado_project> 分目錄（單 repo 腳本的預設只用 repo 名，跨專案會撞名）
MIRRORS_BASE="${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors"
USER_ROOT=1
if [ -z "$WORKDIR_ROOT" ]; then
  USER_ROOT=0
  SERVER_NAME="$(python3 "$CFG" project-server "$CONFIG" "$SERVERS" 2>/dev/null | cut -d$'\x1f' -f1 || true)"
  [ -n "$SERVER_NAME" ] || SERVER_NAME="$(python3 "$CFG" get "$CONFIG" server 2>/dev/null || true)"
  ADO_PROJECT="$(python3 "$CFG" get "$CONFIG" ado_project 2>/dev/null || true)"
  # 斜線會多切一層目錄，換成底線；讀不到時用 _unset 佔位（仍與其他專案分開，但請補齊設定）
  SERVER_NAME="${SERVER_NAME//\//_}"; ADO_PROJECT="${ADO_PROJECT//\//_}"
  WORKDIR_ROOT="$MIRRORS_BASE/${SERVER_NAME:-_unset}/${ADO_PROJECT:-_unset}"
fi
echo "工作 clone 根目錄：$WORKDIR_ROOT"
PENDING_FILE="$WORKDIR_ROOT/pending-protected-pushes.sh"

# 舊版預設路徑（mirrors/<gitea 網址 basename>.git）若存在且遠端與本次相同，提示可改用新路徑；不搬檔、不刪檔
hint_old_path() {
  local gitea="$1" ado="$2" base old
  [ "$USER_ROOT" -eq 0 ] || return 0
  base="$(basename "${gitea%/}")"; base="${base%.git}"
  old="$MIRRORS_BASE/${base}.git"
  [ -f "$old/config" ] || return 0
  if [ "$(git --git-dir="$old" remote get-url gitea 2>/dev/null || true)" = "$gitea" ] &&
     [ "$(git --git-dir="$old" remote get-url ado 2>/dev/null || true)" = "$ado" ]; then
    echo "提示：舊預設路徑 $old 的遠端與本次相同；現已改用 $WORKDIR_ROOT 下的工作 clone，舊目錄可自行決定是否保留。"
  fi
}

# 組出單 repo 腳本的參數（以陣列傳遞，路徑含空白也不會被拆開）
run_one() {
  local name="$1" gitea="$2" ado="$3" branches="$4" key="$5" mode="$6"
  local args=(--gitea "$gitea" --ado "$ado")
  [ -n "$branches" ] && args+=(--branches "$branches")
  [ -n "$key" ] && args+=(--key "$key")
  [ -n "$PROTECTED" ] && args+=(--protected "$PROTECTED")
  args+=(--workdir "$WORKDIR_ROOT/${name}.git")
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
  hint_old_path "$gitea" "$ado"
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
PENDING=""   # 每行：repo 名稱<TAB>單 repo 腳本印出的保護分支推送指令
PROCESSED="" # 本次第二輪處理過的 repo（寫進待推檔的檔頭）

# 彙整保護分支待推指令：印在最後並寫成可執行檔（不執行）；沒有待推時不產生檔案
report_pending() {
  if [ -z "$PENDING" ]; then
    if [ -f "$PENDING_FILE" ]; then
      echo "注意：本次沒有保護分支待推，但 $PENDING_FILE 是先前執行留下的，內容已過期，請勿執行。"
    fi
    return 0
  fi
  echo "================ 保護分支待推指令（腳本未執行，請確認後自行執行）"
  printf '%s\n' "$PENDING" | while IFS=$'\t' read -r n cmd; do
    [ -n "$n" ] || continue
    echo "[$n] $cmd"
  done
  mkdir -p "$WORKDIR_ROOT"
  {
    echo "#!/usr/bin/env bash"
    echo "# 由 sync-all-repos.sh 於 $(date '+%Y-%m-%d %H:%M:%S') 產生。"
    echo "# 涵蓋 repo：${PROCESSED# }（本檔每次產生都會整個覆寫，只含該次處理的 repo）"
    echo "# 以下為保護分支的推送指令，腳本「未執行」；確認無誤後再自行執行本檔或逐行貼上。"
    echo "# 只會快轉推送；若期間 ADO 已有新 commit，推送會被遠端拒絕，請重跑 sync-all-repos.sh 預覽。"
    echo "set -euo pipefail"
    printf '%s\n' "$PENDING" | while IFS=$'\t' read -r n cmd; do
      [ -n "$n" ] || continue
      echo "# $n"
      echo "$cmd"
    done
  } >| "$PENDING_FILE"
  chmod +x "$PENDING_FILE"
  echo "已寫成：$PENDING_FILE"
}

while IFS=$'\x1f' read -r name role gitea ado branches key; do
  [ -n "$name" ] || continue
  echo "### 推送 ${name}（${role}）"
  PROCESSED="$PROCESSED $name"
  if out="$(run_one "$name" "$gitea" "$ado" "$branches" "$key" apply 2>&1)"; then rc=0; else rc=$?; fi
  printf '%s\n' "$out"
  # 擷取單 repo 腳本印出的保護分支指令（格式：兩格縮排＋[GIT_SSH_COMMAND=…] git --git-dir=<%q 路徑> push ado <sha>:refs/heads/<分支>）
  while IFS= read -r line; do
    PENDING="${PENDING}${name}"$'\t'"${line#  }"$'\n'
  done < <(printf '%s\n' "$out" | grep -E '^  .*git --git-dir=.* push ado [0-9a-f]+:refs/heads/' || true)
  if [ "$rc" -ne 0 ]; then
    echo "錯誤：repo $name 推送失敗，後續 repo 不再處理" >&2
    report_pending
    exit 1
  fi
  echo
done <<<"$ORDERED"
report_pending
exit 0
