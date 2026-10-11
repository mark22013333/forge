#!/bin/bash
#---------------------------------------------------------------------
# 一鍵觸發 ADO CD：把 Gitea 上正式分支的最新版同步到 ADO，再移動 ADO 的部署分支 → pipeline 自動建置並部署（cd-ssh-vm.md）。
#
# 驗證狀態：未驗證。由一個實際專案的一鍵腳本參數化而來；該版本只跑過預覽與「本機有未推送 commit 時中止」，
#           未以 --apply 實際推送。本檔改寫後也未執行過。
#
# 用法（先把程式碼推上 Gitea 的正式分支）：
#   push-deploy-branch.sh            # 預覽要做的事，不推任何東西
#   push-deploy-branch.sh --apply    # 實際推送並觸發部署
#
# 依序（任何一步失敗即中止）：
#   1. 確認本機正式分支沒有尚未推上 Gitea 的 commit（避免部署出去的版本與預期不同）
#   2. 依賴 repo：ADO 的正式分支快轉到 Gitea 的版本
#   3. 本 repo：ADO 的正式分支快轉到 Gitea 的版本
#   4. 本 repo：ADO 的部署分支快轉到同一版 → 觸發部署（最後才推）
# 只做快轉、絕不 force push；ADO 端分叉就停下交給人處理。Gitea 只讀不寫；部署分支只存在 ADO。
# 用 SSH 金鑰推送（ADO 內建 SSH 只收 RSA，踩坑 4），不需要 ADO 權杖。
#
# 放進專案 repo 的 bin/ 後，填下方「專案設定」；實際值不要寫回公開範本。
#---------------------------------------------------------------------
set -euo pipefail

#============================ 專案設定 ============================
APP_REPO='<主 repo 在 ADO 的名稱>'
DEP_REPOS=''                      # 依賴 repo（與本 repo 同層目錄、同在 ADO 專案內，空白分隔）；沒有就留空
BRANCH='<正式分支，例：main>'
DEPLOY_BRANCH='<部署分支，例：SIT>'
ADO_SSH_BASE=${ADO_SSH_BASE:-'ssh://<ado 主機>:22/<collection>/<ADO 專案>/_git'}
ADO_KEY=${ADO_KEY:-"$HOME/.ssh/<ADO 用的 RSA 金鑰>"}
ADO_WEB='http://<ado 主機>:<port>/<collection>/<ADO 專案>/_build'
#==================================================================

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORKSPACE=$(cd "$REPO_ROOT/.." && pwd)
case "$*" in
    "")        APPLY=0 ;;
    "--apply") APPLY=1 ;;
    *)         sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac

die()  { echo "✗ $*" >&2; exit 1; }
info() { echo "▸ $*"; }

[ -f "$ADO_KEY" ] || die "找不到 ADO 的 SSH 金鑰 ${ADO_KEY}（可用環境變數 ADO_KEY 指定）"
export GIT_SSH_COMMAND="ssh -i $ADO_KEY -o IdentitiesOnly=yes -o IdentityAgent=none -o LogLevel=ERROR -o BatchMode=yes"

# $1=本機 repo 路徑 $2=顯示名稱；印出 Gitea 上正式分支的 SHA，並確認本機沒有未推送的 commit
gitea_tip() {
    local dir=$1 name=$2 sha local_sha
    [ -d "$dir/.git" ] || die "找不到 ${name} 的本機 repo：$dir"
    git -C "$dir" fetch -q origin "$BRANCH" || die "${name}：無法從 Gitea 取得 ${BRANCH}（需連 VPN？）"
    sha=$(git -C "$dir" rev-parse FETCH_HEAD)
    if local_sha=$(git -C "$dir" rev-parse -q --verify "refs/heads/$BRANCH"); then
        if [ -n "$(git -C "$dir" rev-list "$sha..$local_sha")" ]; then
            die "${name}：本機 $BRANCH 有 $(git -C "$dir" rev-list --count "$sha..$local_sha") 個 commit 還沒推上 Gitea，請先 git push origin $BRANCH"
        fi
    fi
    echo "$sha"
}

# $1=本機 repo 路徑 $2=ADO repo 名稱 $3=SHA $4=ADO 分支；只做快轉
ff_push() {
    local dir=$1 name=$2 sha=$3 ref=$4 url="$ADO_SSH_BASE/$2" cur
    cur=$(git -C "$dir" ls-remote "$url" "refs/heads/$ref" | cut -f1) || die "${name}：無法連線 ADO"
    if [ "$cur" = "$sha" ]; then info "$name ${ref}：已是 ${sha:0:9}，略過"; return 0; fi
    if [ -n "$cur" ]; then
        git -C "$dir" cat-file -e "$cur^{commit}" 2>/dev/null || git -C "$dir" fetch -q "$url" "refs/heads/$ref" \
            || die "$name ${ref}：無法取得 ADO 上的 ${cur:0:9}"
        git -C "$dir" merge-base --is-ancestor "$cur" "$sha" \
            || die "$name ${ref}：ADO 上的 ${cur:0:9} 不是 ${sha:0:9} 的祖先（已分叉），不 force push，請人工處理"
    fi
    if [ $APPLY -eq 0 ]; then info "（預覽）$name ${ref}：${cur:0:9}${cur:+ }→ ${sha:0:9}"; return 0; fi
    git -C "$dir" push -q "$url" "$sha:refs/heads/$ref" || die "$name ${ref}：推送失敗"
    [ "$(git -C "$dir" ls-remote "$url" "refs/heads/$ref" | cut -f1)" = "$sha" ] || die "$name ${ref}：推送後 ADO 的 SHA 不符"
    info "$name ${ref}：${cur:0:9}${cur:+ }→ ${sha:0:9}（已確認）"
}

# 1. 先取齊所有 Gitea 版本、確認都已推送，再開始動 ADO
dep_shas=""
for dep in $DEP_REPOS; do
    s=$(gitea_tip "$WORKSPACE/$dep" "$dep"); dep_shas="$dep_shas $dep=$s"
done
APP_SHA=$(gitea_tip "$REPO_ROOT" "$APP_REPO")
info "要部署的版本：$APP_REPO ${APP_SHA:0:9}「$(git -C "$REPO_ROOT" log -1 --format=%s "$APP_SHA")」"

# 2. 依賴 repo
for pair in $dep_shas; do
    ff_push "$WORKSPACE/${pair%%=*}" "${pair%%=*}" "${pair#*=}" "$BRANCH"
done
# 3、4. 本 repo：正式分支 → 部署分支（部署分支最後推，推了才會觸發部署）
ff_push "$REPO_ROOT" "$APP_REPO" "$APP_SHA" "$BRANCH"
ff_push "$REPO_ROOT" "$APP_REPO" "$APP_SHA" "$DEPLOY_BRANCH"

if [ $APPLY -eq 0 ]; then
    info "預覽完成，未推送任何東西。確認無誤後加 --apply"
else
    info "已觸發 ADO CD（$APP_REPO 的 $DEPLOY_BRANCH 分支）；進度：$ADO_WEB"
fi
