<#
  Bitwarden 凭据导入脚本（v5 参数化版）
  ============================
  请在你自己的终端（PowerShell 7 / pwsh）运行本脚本。

  为什么由你运行：
    导入需要解锁 vault，解锁必须输入主密码。让脚本在你的终端交互输入，
    主密码与 BW_SESSION 不会经过任何日志、不会进入聊天记录、不会被 Agent 读到。

  前置：
    1) bw CLI 已安装（scoop install bitwarden-cli）
    2) 账号已登录且会话有效：bw login   （若报 invalid_grant / not logged in，先重新登录）
  用法：
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -DataRoot <数据根>
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -DataRoot <数据根> -DryRun
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -DataRoot <数据根> -Account you@example.com
#>
param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [string]$Account = "",
    [string]$BwPath = "bw",
    [string]$File = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$MapDir = Join-Path $DataRoot "vault\map"
$Bw = $BwPath

if (-not (Get-Command $Bw -ErrorAction SilentlyContinue)) {
    if (-not (Test-Path $Bw)) { throw "找不到 bw：$Bw（用 -BwPath 指定绝对路径）" }
}
if (-not (Test-Path $MapDir)) { throw "找不到目录：$MapDir（先跑 init_workspace.py / sanitize.py --apply）" }

if ($File) {
    $importFile = Get-Item $File
}
else {
    $importFile = Get-ChildItem $MapDir -Filter "bw-import-*.json" -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime | Select-Object -Last 1
}
if (-not $importFile) { throw "未找到 bw-import-*.json，请先运行 sanitize.py --apply" }

Write-Host "=== 导入文件 ===" -ForegroundColor Cyan
Write-Host "  $($importFile.FullName)"
$payload = Get-Content $importFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "  条目数：$($payload.items.Count)" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 预检登录态：确保真正处于 locked/unlocked，而不是 unauthenticated
# ---------------------------------------------------------------------------
Write-Host "`n=== 当前登录状态 ===" -ForegroundColor Cyan
$statusObj = & $Bw status | ConvertFrom-Json
Write-Host "  status    = $($statusObj.status)"
Write-Host "  serverUrl = $($statusObj.serverUrl)"

if ($statusObj.status -eq "unauthenticated") {
    Write-Host "`n[未登录] 会话无效，请先重新登录后重跑本脚本：" -ForegroundColor Red
    $loginHint = if ($Account) { "bw logout; if ($?) { bw login $Account }" } else { "bw logout; bw login <你的邮箱>" }
    Write-Host "    $loginHint" -ForegroundColor Yellow
    return
}

if ($DryRun) {
    Write-Host "`n[-DryRun] 仅预览，未解锁、未导入。" -ForegroundColor Yellow
    return
}

# ---------------------------------------------------------------------------
# 获取会话
#   - 若 vault 已 unlocked 且父终端传入了 BW_SESSION：直接复用，不再 unlock。
#     （对已解锁的 vault 重复 bw unlock --raw 可能返回空/仅警告，导致误判「解锁失败」）
#   - 否则交互输入主密码解锁。
# ---------------------------------------------------------------------------
if ($statusObj.status -eq "unlocked" -and $env:BW_SESSION) {
    Write-Host "  已是 unlocked 且检测到 BW_SESSION → 复用现有会话（跳过 unlock）。" -ForegroundColor Green
}
else {
    Remove-Item Env:\BW_SESSION -ErrorAction SilentlyContinue
    Write-Host "`n=== 解锁 vault（请输入主密码）===" -ForegroundColor Cyan
    $sessionRaw = (& $Bw unlock --raw 2>&1 | Out-String)
    $session = (($sessionRaw -split "`r?`n") |
                Where-Object { $_ -match '^[A-Za-z0-9+/=._-]{20,}$' } |
                Select-Object -Last 1)
    if (-not $session) {
        Write-Host "`n[解锁失败] 未从 bw unlock 输出中取得有效会话。" -ForegroundColor Red
        Write-Host "  bw unlock 原始输出：" -ForegroundColor DarkGray
        (($sessionRaw -replace "`r?`n", "`n") -split "`n") | ForEach-Object {
            if ($_ -ne "") { Write-Host "    $_" -ForegroundColor DarkGray }
        }
        Write-Host "  排查建议：" -ForegroundColor Yellow
        Write-Host "    a) status=unauthenticated → 先 bw logout; bw login <邮箱>" -ForegroundColor Yellow
        Write-Host "    b) status=locked 却弹不出提示 → 请在父终端手动解锁后重跑：" -ForegroundColor Yellow
        Write-Host "         `$env:BW_SESSION = (bw unlock --raw) ; pwsh -File <本脚本> -DataRoot $DataRoot" -ForegroundColor Yellow
        return
    }
    $env:BW_SESSION = $session
    Write-Host "  已解锁（session 仅存于本进程环境变量，脚本结束即清除）" -ForegroundColor Green
}

$imported = 0
try {
    Write-Host "`n=== 同步 ===" -ForegroundColor Cyan
    & $Bw sync
    if (-not $?) { throw "bw sync 失败（会话可能无效）" }

    Write-Host "`n=== 导入条目 ===" -ForegroundColor Cyan
    & $Bw import bitwardenjson $importFile.FullName
    if (-not $?) { throw "bw import 失败" }

    Write-Host "`n=== 回读条目，生成 token↔条目 映射 ===" -ForegroundColor Cyan
    $items = & $Bw list items --search "notes/"
    try { $imported = (@($items | ConvertFrom-Json)).Count } catch { $imported = -1 }

    if ($imported -gt 0) {
        $itemsFile = Join-Path $MapDir "bw-items.json"
        $items | Out-File $itemsFile -Encoding utf8
        Write-Host "  已写入：$itemsFile（$imported 条）" -ForegroundColor Green
        Write-Host "  下一步：python scripts\backfill.py --data-root `"$DataRoot`" --apply" -ForegroundColor Yellow
    }
    else {
        Write-Host "  ⚠ 回读到 0 条，未写 bw-items.json（避免污染 backfill）。请检查导入是否成功/搜索前缀。" -ForegroundColor Red
    }
}
finally {
    if ($env:BW_SESSION) {
        Write-Host "`n=== 锁定 vault ===" -ForegroundColor Cyan
        & $Bw lock
        Remove-Item Env:\BW_SESSION -ErrorAction SilentlyContinue
        Write-Host "  已锁定，session 已清除。" -ForegroundColor Green
    }
}