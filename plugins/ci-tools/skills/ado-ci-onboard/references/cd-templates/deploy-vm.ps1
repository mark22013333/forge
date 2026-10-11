# CD：自架 Windows agent 經 SSH 把產物部署到 Linux VM（cd-ssh-vm.md）
#
# 驗證狀態：未驗證。由一個 Tomcat＋war 實際專案的部署腳本去識別化、參數化而來；該專案的 dry-run 尚未成功過、也未正式部署，
#           本骨架本身也沒有在 ADO 執行過。第一次套用請先以 -DryRun 跑，並照 SKILL.md 第 5 章用 REST API 看 log。
#
# 本檔只負責 agent 端：預檢、（可選）產物比對、分段上傳＋sha256 驗證、上傳遠端腳本、在 VM 以 setsid nohup 背景執行並追蹤結果。
# VM 上的停止、備份、切換、健康檢查、回滾由專案的遠端腳本負責，契約見 cd-ssh-vm.md「遠端腳本契約」：
#   bash <遠端腳本> precheck                      → 輸出 key=value（至少 locked、disk_avail_kb）
#   bash <遠端腳本> deploy <產物> <sha256> <commit> → 結束前把 OK／ROLLED_BACK／ABORTED／MANUAL 寫進 $RESULT_FILE
#
# 需求：Windows PowerShell 5.1、Windows 內建 OpenSSH（C:\Windows\System32\OpenSSH\ssh.exe／scp.exe）。
# 注意：本檔必須存成 UTF-8 with BOM。Windows PowerShell 5.1 讀無 BOM 的 .ps1 會用系統 ANSI 編碼解讀，
#       中文變亂碼並吃掉引號，整支腳本無法解析。
param(
    [Parameter(Mandatory = $true)][string]$Artifact,        # 要部署的產物（DownloadBuildArtifacts 下載的檔案）
    [Parameter(Mandatory = $true)][string]$KeyFile,         # SSH 私鑰（DownloadSecureFile 的 secureFilePath）
    [Parameter(Mandatory = $true)][string]$KnownHostsFile,  # 釘選的主機金鑰檔（repo 內，StrictHostKeyChecking=yes）
    [Parameter(Mandatory = $true)][string]$RemoteScript,    # 遠端腳本（bash，於 VM 執行；repo 內路徑）
    [Parameter(Mandatory = $true)][string]$VmHost,
    [Parameter(Mandatory = $true)][string]$VmUser,
    [Parameter(Mandatory = $true)][string]$RemoteStaging,   # VM 上的暫存目錄（上傳的產物、腳本、log、result 檔）
    [string]$AppName = 'app',                               # 暫存檔名前綴
    [int]$VmPort = 22,
    [string]$Commit = 'unknown',                            # 寫進部署紀錄
    [hashtable]$RemoteEnv = @{},                            # 帶給遠端腳本的常數（以 NAME='value' 寫在腳本前面）
    [int]$ChunkSizeMB = 3,                                  # 分段上傳每段大小（經 VPN 單一連線持續上傳可能卡死）
    [int]$ChunkTimeoutSec = 15,
    [int]$ChunkRetries = 5,
    [int]$MaxWaitSec = 1800,                                # 追蹤遠端執行的上限
    [switch]$DryRun                                         # 只做預檢，不上傳、不動服務
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Ts = Get-Date -Format 'yyyyMMdd-HHmmss'
$Prefix = "$RemoteStaging/$AppName-$Ts"
$ResultFile = "$Prefix.result"

function Info($m) { Write-Host "▸ $m" }
function Warn($m) { Write-Host "##vso[task.logissue type=warning]$m" }
function Die($m)  { Write-Host "##vso[task.logissue type=error]$m"; exit 1 }

#---------------- SSH 準備：私鑰權限收緊、主機金鑰釘選 ----------------
$Ssh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$Scp = Join-Path $env:WINDIR 'System32\OpenSSH\scp.exe'
foreach ($t in $Ssh, $Scp) { if (-not (Test-Path $t)) { Die "找不到 $t" } }
if (-not (Test-Path $KnownHostsFile)) { Die "找不到主機金鑰檔 $KnownHostsFile" }
$Work = Join-Path $env:AGENT_TEMPDIRECTORY "deploy-vm-$Ts"
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Key = Join-Path $Work 'id_deploy'
Copy-Item $KeyFile $Key
# Windows OpenSSH 拒絕其他帳號可讀的私鑰：移除繼承、只留目前帳號
& icacls $Key /inheritance:r /grant:r "$(whoami):R" | Out-Null
if ($LASTEXITCODE -ne 0) { Die '無法設定私鑰檔權限' }
# 不設 ConnectTimeout：Windows 版 OpenSSH 8.1 設了它之後，每次連線都會等滿這個秒數才回報成功（踩坑 44）；
# 分段上傳另有 ChunkTimeoutSec 的逾時保護
$SshOpts = @('-i', $Key, '-P', "$VmPort", '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
             '-o', "UserKnownHostsFile=$KnownHostsFile", '-o', 'StrictHostKeyChecking=yes',
             '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=3', '-o', 'LogLevel=ERROR')
$SshOnlyOpts = $SshOpts | ForEach-Object { if ($_ -eq '-P') { '-p' } else { $_ } }   # ssh 用 -p、scp 用 -P
$Target = "$VmUser@$VmHost"

# PowerShell 5.1 在 ErrorActionPreference=Stop 下會把原生程式的 stderr 當成例外，呼叫 ssh／scp 時暫改為 Continue
function Invoke-Ssh([string]$cmd) {
    $ErrorActionPreference = 'Continue'
    $out = & $Ssh @SshOnlyOpts $Target $cmd 2>&1
    $script:SshExit = $LASTEXITCODE
    return $out
}
function Send-File([string]$local, [string]$remote) {
    $ErrorActionPreference = 'Continue'
    & $Scp @SshOpts -q $local "${Target}:$remote" 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Die "上傳失敗：$local → $remote（服務未變動）" }
}
# 以 LF 換行、UTF-8 無 BOM 寫檔（checkout 可能是 CRLF，bash 會因 \r 出錯；也避免管線把中文轉成 ASCII）
function Write-LfFile([string]$path, [string]$text) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, ($text -replace "`r`n", "`n"), $enc)
}

#---------------- 組遠端腳本（常數＋專案的遠端腳本）並上傳 ----------------
if (-not (Test-Path $RemoteScript)) { Die "找不到遠端腳本 $RemoteScript" }
$env2 = [ordered]@{ TS = $Ts; ACTOR = "ado-pipeline@$env:COMPUTERNAME"; RESULT_FILE = $ResultFile; STAGING = $RemoteStaging }
foreach ($k in $RemoteEnv.Keys) { $env2[$k] = [string]$RemoteEnv[$k] }
$hdr = "#!/bin/bash`n"
foreach ($k in $env2.Keys) {
    $v = [string]$env2[$k]
    if ($v.Contains("'")) { Die "常數 $k 含單引號，無法安全帶入" }
    $hdr += "export $k='$v'`n"
}
$CoreLocal = Join-Path $Work 'remote.sh'
Write-LfFile $CoreLocal ($hdr + [System.IO.File]::ReadAllText($RemoteScript, [System.Text.Encoding]::UTF8))
$CoreRemote = "$Prefix.remote.sh"
Invoke-Ssh "mkdir -p $RemoteStaging" | Out-Null
if ($SshExit -ne 0) { Die "無法連線 $Target（服務未變動）" }
Send-File $CoreLocal $CoreRemote

#---------------- 本機端預檢 ----------------
if (-not (Test-Path $Artifact)) { Die "產物不存在：$Artifact" }
$Size = (Get-Item $Artifact).Length
$Sha = (Get-FileHash -Algorithm SHA256 $Artifact).Hash.ToLower()
Info ("產物 {0}（{1} MB、sha256 {2}…）" -f (Split-Path $Artifact -Leaf), [math]::Round($Size / 1MB), $Sha.Substring(0, 16))

#---------------- 遠端預檢（唯讀） ----------------
Info '遠端預檢'
$Pre = @{}
foreach ($line in (Invoke-Ssh "bash $CoreRemote precheck")) {
    $s = [string]$line; $i = $s.IndexOf('=')
    if ($i -gt 0) { $Pre[$s.Substring(0, $i)] = $s.Substring($i + 1) }
}
if ($SshExit -ne 0 -or $Pre.Count -eq 0) { Die '遠端預檢失敗（服務未變動）' }
if ($Pre['locked'] -eq '1') { Die '另一個部署正在進行中' }
$NeedKb = [int64]($Size * 3 / 1024)
if ($Pre.ContainsKey('disk_avail_kb') -and [int64]$Pre['disk_avail_kb'] -lt $NeedKb) { Die "VM 磁碟可用 $($Pre['disk_avail_kb'])KB，不足產物×3（${NeedKb}KB）" }

#---------------- （可選）產物比對：產品專屬範例 ----------------
# 實際專案在這裡比對「新 war 主 jar 的類別清單」與 VM 現行版本，新版少了任何類別就中止，防止用錯分支建置把功能拿掉。
# 需要的話：遠端腳本另提供唯讀子命令列出現行清單，本機用 System.IO.Compression 讀產物取得新清單後比對。
# 本骨架不預設實作（判斷條件因產品而異）。

if ($DryRun) { Info 'DryRun：預檢完成，未上傳、未變動任何東西'; exit 0 }

#---------------- 分段上傳並驗證 ----------------
$ChunkSize = $ChunkSizeMB * 1MB
$Staged = "$Prefix.artifact"
$ChunkDir = "$Staged.chunks"
$Parts = [int][math]::Ceiling($Size / $ChunkSize)
Info "分段上傳到 ${Target}:$ChunkDir（$Parts 段，每段 ${ChunkSizeMB}MB）"
Invoke-Ssh "mkdir -p $ChunkDir" | Out-Null
if ($SshExit -ne 0) { Die '無法在 VM 建立暫存目錄（服務未變動）' }
$fs = [System.IO.File]::OpenRead($Artifact)
$buf = New-Object byte[] $ChunkSize
$chunkLocal = Join-Path $Work 'chunk.bin'
$started = Get-Date
try {
    for ($i = 0; $i -lt $Parts; $i++) {
        $n = $fs.Read($buf, 0, $ChunkSize)
        $data = New-Object byte[] $n
        [Array]::Copy($buf, $data, $n)
        [System.IO.File]::WriteAllBytes($chunkLocal, $data)
        $name = '{0}/{1:D5}' -f $ChunkDir, $i
        $ok = $false
        for ($try = 1; $try -le $ChunkRetries -and -not $ok; $try++) {
            $argList = @($SshOpts + @('-q', "`"$chunkLocal`"", "${Target}:$name.tmp")) | ForEach-Object { if ($_ -match '\s' -and $_ -notmatch '^"') { "`"$_`"" } else { $_ } }
            $p = Start-Process -FilePath $Scp -ArgumentList $argList -NoNewWindow -PassThru
            # 〔推測〕Windows PowerShell 5.1 未先取得 Handle 時，程序結束後 ExitCode 可能讀到空值；先存取一次 Handle 避免
            $null = $p.Handle
            # 逾時：scp 會再開 ssh 子程序，用 taskkill /T 連同子程序一起結束
            if (-not $p.WaitForExit($ChunkTimeoutSec * 1000)) { & taskkill /T /F /PID $p.Id 2>&1 | Out-Null; $p.WaitForExit() }
            elseif ($p.ExitCode -eq 0) {
                # 寫到暫存名、確認大小後才改名，避免被殺掉的舊連線殘留寫入
                $sz = Invoke-Ssh "stat -c %s $name.tmp && mv $name.tmp $name"
                if ($SshExit -eq 0 -and ([string]($sz | Select-Object -First 1)).Trim() -eq [string]$n) { $ok = $true }
            }
            if (-not $ok -and $try -lt $ChunkRetries) { Warn "第 $($i + 1) 段失敗，重傳（$try/$ChunkRetries）"; Start-Sleep -Seconds 2 }
        }
        if (-not $ok) { Die "第 $($i + 1) 段嘗試 $ChunkRetries 次仍失敗，上傳中止（服務未變動）" }
        if ((($i + 1) % 20) -eq 0 -or ($i + 1) -eq $Parts) {
            Info ("  已上傳 {0}/{1} 段（{2}%，{3:N0}s）" -f ($i + 1), $Parts, [int](($i + 1) * 100 / $Parts), ((Get-Date) - $started).TotalSeconds)
        }
    }
} finally { $fs.Dispose() }
# 只取五位數字的正式段名，避免把殘留的 .tmp 組進去
Invoke-Ssh "cat $ChunkDir/[0-9][0-9][0-9][0-9][0-9] > $Staged.part && rm -rf $ChunkDir" | Out-Null
if ($SshExit -ne 0) { Die '遠端組合分段失敗（服務未變動）' }
$remoteSha = ([string](Invoke-Ssh "sha256sum $Staged.part | cut -d' ' -f1" | Select-Object -First 1)).Trim()
if ($remoteSha -ne $Sha) {
    Invoke-Ssh "mv $Staged.part $Staged.corrupt" | Out-Null
    Die "上傳後 sha256 不符（服務未變動），損壞檔留在 $Staged.corrupt"
}
Invoke-Ssh "mv $Staged.part $Staged" | Out-Null
Info '上傳完成，sha256 一致'

#---------------- 在 VM 背景執行遠端腳本並追蹤（agent 斷線不影響遠端） ----------------
# launch／follow 先寫成檔案再執行，避免在 ssh 指令列裡處理多層引號
$RunLog = "$Prefix.run.log"; $PidFile = "$Prefix.pid"
$launch = @"
#!/bin/bash
setsid nohup bash -c 'echo `$`$ > $PidFile; exec bash $CoreRemote deploy $Staged $Sha $Commit' > $RunLog 2>&1 < /dev/null &
"@
$follow = @"
#!/bin/bash
sleep 1; tail -n +1 -f $RunLog & tp=`$!
n=0
while [ ! -s $ResultFile ]; do
    if [ -f $PidFile ] && ! kill -0 `$(cat $PidFile) 2>/dev/null; then sleep 1; [ -s $ResultFile ] || echo CRASHED > $ResultFile; fi
    sleep 2; n=`$((n + 2))
    [ `$n -gt $MaxWaitSec ] && break
done
sleep 1; kill `$tp 2>/dev/null; exit 0
"@
Write-LfFile (Join-Path $Work 'launch.sh') $launch
Write-LfFile (Join-Path $Work 'follow.sh') $follow
Send-File (Join-Path $Work 'launch.sh') "$Prefix.launch.sh"
Send-File (Join-Path $Work 'follow.sh') "$Prefix.follow.sh"
Invoke-Ssh "bash $Prefix.launch.sh" | Out-Null
if ($SshExit -ne 0) { Die '無法在 VM 啟動部署程序（服務未變動）' }
$ErrorActionPreference = 'Continue'
& $Ssh @SshOnlyOpts $Target "bash $Prefix.follow.sh" 2>&1 | Out-Host
$res = ([string](Invoke-Ssh "cat $ResultFile" | Select-Object -First 1)).Trim()
if (-not $res) { $res = 'UNKNOWN' }
Write-Host ''
switch ($res) {
    'OK'          { Info '部署成功'; exit 0 }
    'ROLLED_BACK' { Write-Host '##vso[task.logissue type=error]新版失敗，已自動回滾到上一版'; exit 2 }
    'ABORTED'     { Write-Host '##vso[task.logissue type=error]部署中止，服務未變動'; exit 3 }
    'UNKNOWN'     { Write-Host "##vso[task.logissue type=error]沒拿到結果（斷線或追蹤逾時），遠端可能仍在執行；查看 VM 上的 $RunLog"; exit 4 }
    default       { Write-Host "##vso[task.logissue type=error]結果：$res —— 需人工介入；遠端執行紀錄 $RunLog"; exit 4 }
}
