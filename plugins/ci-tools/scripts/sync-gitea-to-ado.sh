#!/usr/bin/env bash
# sync-gitea-to-ado.sh — Gitea（主）→ Azure DevOps Server（CI 鏡像）單向快轉同步
#
# 預設只預覽：列出每個分支的狀態，不推任何東西。
# 加 --apply 才推送，而且只推「可快轉」與「新分支」；任一選定分支已分叉就整批不推。
# 保護分支（預設 main,master,production）一律不推，只印出可貼上的指令讓使用者自己執行。
# 不使用任何強制推送。
#
# 用法：
#   sync-gitea-to-ado.sh --gitea <url> --ado <url> [選項]
#
# 選項：
#   --gitea <url>        Gitea repo 網址（必填），例：ssh://git@gitea.example.com/team/project-a.git
#   --ado <url>          ADO repo 網址（必填），例：ssh://ado-host:22/Collection/project-a/_git/project-a
#   --branches <a,b,...> 只處理這些分支（預設：兩邊所有分支）
#   --key <path>         SSH 私鑰路徑；指定後只用這把金鑰，不讀 ssh-agent
#   --workdir <path>     本機 bare 工作 clone 路徑
#                        （預設：${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors/<repo>.git）
#   --protected <a,b>    保護分支清單（預設：main,master,production）
#   --apply              實際推送（未帶時只預覽）
#   -h, --help           顯示說明
#
# 結束碼：0 正常；1 參數或執行錯誤、或指定的分支兩邊都不存在；2 有分叉，--apply 未推任何分支
#
# 已知限制：狀態是在 fetch 當下判定的。若 fetch 之後、推送之前有人直接推到 ADO，
#           該分支的推送會被遠端以非快轉拒絕，腳本在那一步中止，之前已推的分支不會做 ls-remote 驗證。
#           遇到時重跑一次預覽即可看到最新狀態。

set -euo pipefail

usage() { sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'; }

GITEA=""
ADO=""
BRANCHES=""
KEY=""
WORKDIR=""
PROTECTED="main,master,production"
APPLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --gitea) GITEA="${2:-}"; shift 2 ;;
    --ado) ADO="${2:-}"; shift 2 ;;
    --branches) BRANCHES="${2:-}"; shift 2 ;;
    --key) KEY="${2:-}"; shift 2 ;;
    --workdir) WORKDIR="${2:-}"; shift 2 ;;
    --protected) PROTECTED="${2:-}"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知參數：$1" >&2; usage >&2; exit 1 ;;
  esac
done

if [ -z "$GITEA" ] || [ -z "$ADO" ]; then
  echo "錯誤：--gitea 與 --ado 為必填" >&2
  exit 1
fi

# 踩坑 4：只用指定金鑰，排除 ssh-agent 內其他金鑰的干擾
SSH_PREFIX=""
if [ -n "$KEY" ]; then
  [ -f "$KEY" ] || { echo "錯誤：找不到金鑰 $KEY" >&2; exit 1; }
  # git 會用 shell 解析 GIT_SSH_COMMAND，路徑要跳脫（含空白或引號時才不會壞）
  GIT_SSH_COMMAND="ssh -i $(printf '%q' "$KEY") -o IdentitiesOnly=yes -o IdentityAgent=none"
  export GIT_SSH_COMMAND
  SSH_PREFIX="GIT_SSH_COMMAND=$(printf '%q' "$GIT_SSH_COMMAND") "
fi

# 踩坑 16：工作 clone 放持久目錄，不放 /tmp（macOS 會定期清掉久未存取的檔案）
if [ -z "$WORKDIR" ]; then
  REPO_NAME="$(basename "${GITEA%/}")"
  REPO_NAME="${REPO_NAME%.git}"
  WORKDIR="${XDG_DATA_HOME:-$HOME/.local/share}/ci-tools/mirrors/${REPO_NAME}.git"
fi
echo "工作 clone：$WORKDIR"

# 既有工作 clone 的遠端若與本次不同，代表路徑撞名（例：兩個專案的 repo 都叫 product），
# 直接中止、不覆蓋，否則會把另一個專案的鏡像靜默改指到這裡
# 網址印出前遮掉 //帳號[:密碼]@（https 網址可能嵌了 PAT）
mask_url() { printf '%s\n' "$1" | sed 's#//[^/@]*@#//***@#'; }
if [ -f "$WORKDIR/config" ]; then
  CONFLICT=0
  for r in gitea ado; do
    url="$GITEA"; [ "$r" = ado ] && url="$ADO"
    # 用 config 讀原始值：remote get-url 會套用 url.<x>.insteadOf 改寫，同一 repo 也會被誤判為不同
    old="$(git --git-dir="$WORKDIR" config --get "remote.$r.url" 2>/dev/null || true)"
    if [ -n "$old" ] && [ "$old" != "$url" ]; then
      [ "$CONFLICT" -eq 0 ] && echo "錯誤：工作 clone 已存在，且遠端網址與本次不同（可能與其他專案撞名），未做任何修改：" >&2
      echo "  $r 既有：$(mask_url "$old")" >&2
      echo "  $r 本次：$(mask_url "$url")" >&2
      CONFLICT=1
    fi
  done
  if [ "$CONFLICT" -eq 1 ]; then
    echo "請改用 --workdir 指定這個 repo 專用的工作 clone 路徑。" >&2
    exit 1
  fi
fi

mkdir -p "$WORKDIR"
# 重跑 init 是安全的：若 HEAD／config 被清掉會補回，既有 objects 與 refs 不受影響
git init -q --bare "$WORKDIR"
G() { git --git-dir="$WORKDIR" "$@"; }

for r in gitea ado; do
  url="$GITEA"; [ "$r" = ado ] && url="$ADO"
  # 網址不同的情況已在上方擋下，這裡只會遇到「相同」或「尚未設定」
  if ! G config --get "remote.$r.url" >/dev/null 2>&1; then
    G remote add "$r" "$url"
  fi
done

G fetch -q --prune gitea '+refs/heads/*:refs/gitea/*'
G fetch -q --prune ado '+refs/heads/*:refs/ado/*'

TMP="$(mktemp -d)"
G for-each-ref --format='%(refname:lstrip=2)' refs/gitea/ >| "$TMP/gitea"
G for-each-ref --format='%(refname:lstrip=2)' refs/ado/ >| "$TMP/ado"

if [ -n "$BRANCHES" ]; then
  echo "$BRANCHES" | tr ',' '\n' | sed '/^$/d' >| "$TMP/selected"
else
  sort -u "$TMP/gitea" "$TMP/ado" >| "$TMP/selected"
fi

is_protected() { echo ",$PROTECTED," | grep -q ",$1,"; }

: >| "$TMP/push"      # 分支 sha
: >| "$TMP/manual"    # 分支 sha（保護分支）
DIVERGED=""
MISSING=""

echo
while IFS= read -r b; do
  gsha="$(G rev-parse -q --verify "refs/gitea/$b" 2>/dev/null || true)"
  asha="$(G rev-parse -q --verify "refs/ado/$b" 2>/dev/null || true)"
  if [ -z "$gsha" ] && [ -z "$asha" ]; then
    state="兩邊皆無"; MISSING="$MISSING $b"
  elif [ -z "$asha" ]; then
    state="新分支"; action=1
  elif [ -z "$gsha" ]; then
    state="僅 ADO"
  elif [ "$gsha" = "$asha" ]; then
    state="一致"
  elif G merge-base --is-ancestor "$asha" "$gsha"; then
    state="可快轉 (領先 $(G rev-list --count "$asha..$gsha"))"; action=1
  elif G merge-base --is-ancestor "$gsha" "$asha"; then
    state="ADO 領先 ($(G rev-list --count "$gsha..$asha"))"
  else
    state="已分叉 (Gitea 獨有 $(G rev-list --count "$asha..$gsha")、ADO 獨有 $(G rev-list --count "$gsha..$asha"))"
    DIVERGED="$DIVERGED $b"
  fi
  printf '%-30s %s\n' "$b" "$state"
  if [ "${action:-0}" = 1 ]; then
    if is_protected "$b"; then
      echo "$b $gsha" >> "$TMP/manual"
    else
      echo "$b $gsha" >> "$TMP/push"
    fi
  fi
  action=0
done < "$TMP/selected"

RC=0
if [ -n "$MISSING" ]; then
  echo
  echo "錯誤：下列分支兩邊都不存在：$MISSING" >&2
  RC=1
fi

if [ "$APPLY" -eq 0 ]; then
  echo
  echo "（預覽模式，未推送任何分支。確認無誤後加 --apply。）"
  exit "$RC"
fi

if [ -n "$DIVERGED" ]; then
  echo
  echo "已分叉，整批不推送：$DIVERGED" >&2
  echo "請先在 Gitea 端處理分叉，或用 --branches 排除這些分支後再執行。" >&2
  exit 2
fi
[ "$RC" -eq 0 ] || exit "$RC"

echo
PUSHED=""
while read -r b sha; do
  [ -n "$b" ] || continue
  echo "推送 $b → $sha"
  G push -q ado "$sha:refs/heads/$b"
  PUSHED="$PUSHED $b"
done < "$TMP/push"

if [ -s "$TMP/manual" ]; then
  echo
  echo "以下為保護分支，腳本不代推，請確認後自行執行："
  while read -r b sha; do
    # 路徑與分支名用 %q 跳脫：這行會被 sync-all-repos.sh 寫進可執行檔，含 $ 或反引號時不可被展開
    echo "  ${SSH_PREFIX}git --git-dir=$(printf '%q' "$WORKDIR") push ado $(printf '%q' "$sha:refs/heads/$b")"
  done < "$TMP/manual"
fi

if [ -n "$PUSHED" ]; then
  echo
  for b in $PUSHED; do
    gs="$(git ls-remote "$GITEA" "refs/heads/$b" | cut -f1)"
    as="$(git ls-remote "$ADO" "refs/heads/$b" | cut -f1)"
    if [ -n "$gs" ] && [ "$gs" = "$as" ]; then v="一致"; else v="不一致"; RC=1; fi
    echo "驗證 $b gitea=$gs ado=$as $v"
  done
fi

exit "$RC"
