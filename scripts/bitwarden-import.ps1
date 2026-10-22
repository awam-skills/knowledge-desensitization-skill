<#
  Bitwarden 凭据导入脚本（v6：目录自动解析版）
  ==========================================
  请在你自己的终端（PowerShell 7 / pwsh）运行本脚本。

  为什么由你运行：
    导入需要解锁 vault，解锁必须输入主密码。让脚本在你的终端交互输入，
    主密码与 BW_SESSION 不会经过任何日志、不会进入聊天记录、不会被 Agent 读到。

  ★ 本脚本要的目录是「**秘密材料目录**」（含 vault\map\ 的那个），
    不是真源、也不是工作副本。三者容易混淆，脚本现在会**自动解析并打印依据**：

      <真源>                    只读原始知识库（G:\Data\knowledge\logseq\main）
     <数据根>/<id>             工作副本：就地脱敏的产物（可上云）
     <秘密材料根>/<id>          秘密材料与运行态（含 vault\map\）← **本脚本要的**
                                缺省 <技能目录>\.km\<id>，绝不外传

    历史包袱说明：参数曾叫 -DataRoot，容易误以为要数据根；现已改名 -SecretDir，
    -DataRoot 仍作为兼容别名保留（含义相同 = 秘密材料目录）。

  解析优先级（自上而下，取第一个含 vault\map\ 的）
    1) -SecretDir <dir>                          （显式；最准确）
    2) -DataRoot <dir>                           （兼容别名，同一含义）
    3) 环境变量 KM_SECRET_DIR
    4) <秘密材料根>\<id>，其中
         <id>          = -Id <源id>，缺省取 sources\*.yaml（仅一个时）
         <秘密材料根>  = -SecretsRoot > sources\<id>.yaml 的 secrets_root
                         > settings.yaml 的 secrets_root > <技能目录>\.km
    5) 扫描 <秘密材料根>\*\ 中任何含 vault\map\ 的目录

  前置：
    1) bw CLI 已安装（scoop install bitwarden-cli）
    2) 账号已登录且会话有效：bw login   （若报 invalid_grant / not logged in，先重新登录）
  用法：
    pwsh -File <技能>\scripts\bitwarden-import.ps1                 # 全自动解析
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -Id logseq-main
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -SecretDir "<秘密材料目录>"
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -DryRun         # 只预览（不解锁、不导入）
    pwsh -File <技能>\scripts\bitwarden-import.ps1 -Account you@example.com
#>
param(
    [Parameter(Position = 0)][string]$SecretDir = "",
    [string]$DataRoot = "",        # 兼容别名（含义同 -SecretDir：秘密材料目录）
    [string]$Id = "",
    [string]$SecretsRoot = "",
    [string]$File = "",
    [string]$Account = "",
    [string]$BwPath = "bw",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---------------------------------------------------------------------------
# 小工具：极简 YAML 取值 / 源 id 枚举 / 目录判定
# ---------------------------------------------------------------------------
function Get-YamlScalar {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        if ($line -match ("^\s*" + [regex]::Escape($Key) + "\s*:\s*(.*?)\s*$")) {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v -eq "" -or $v -eq "null" -or $v -eq "~") { return $null }
            return $v
        }
    }
    return $null
}

function Get-YamlNested {
    param([string]$Path, [string]$Section, [string]$Key)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $inSec = $false
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        if ($line -match "^(\S[^:]*):\s*$") {
            $inSec = ($Matches[1].Trim() -eq $Section)
            continue
        }
        if ($inSec -and $line -match ("^\s+" + [regex]::Escape($Key) + "\s*:\s*(.*?)\s*$")) {
            $v = $Matches[1].Trim().Trim('"').Trim("'")
            if ($v -eq "" -or $v -eq "null" -or $v -eq "~") { return $null }
            return $v
        }
    }
    return $null
}

function Get-SourceIds {
    param([string]$CfgHome)
    if (-not $CfgHome) { return @() }
    $d = Join-Path $CfgHome "sources"
    if (-not (Test-Path -LiteralPath $d)) { return @() }
    return @(Get-ChildItem -LiteralPath $d -Filter *.yaml -ErrorAction SilentlyContinue |
             ForEach-Object { $_.BaseName })
}

function Test-SecretDir {
    param([string]$Dir)
    if (-not $Dir) { return $false }
    return (Test-Path -LiteralPath (Join-Path $Dir "vault\map"))
}

$SkillRoot = Split-Path $PSScriptRoot -Parent
$CfgHome = if ($env:KM_CONFIG_HOME) { $env:KM_CONFIG_HOME }
           elseif ($env:USERPROFILE) { Join-Path $env:USERPROFILE ".knowledge-manager-skills" }
           elseif ($env:HOME) { Join-Path $env:HOME ".knowledge-manager-skills" }
           else { "" }
$SettingsPath = if ($CfgHome) { Join-Path $CfgHome "settings.yaml" } else { Join-Path $SkillRoot "__no_settings__.yaml" }

# ---------------------------------------------------------------------------
# 解析「秘密材料目录」
# ---------------------------------------------------------------------------
$resolvedDir = $null
$resolvedWhy = $null
$tried = New-Object System.Collections.Generic.List[string]

$ids = Get-SourceIds -CfgHome $CfgHome
$id = $Id
if (-not $id -and $ids.Count -eq 1) { $id = $ids[0] }

$sroot = $SecretsRoot
if (-not $sroot -and $id) {
    $srcCfg = Join-Path (Join-Path $CfgHome "sources") "$id.yaml"
    $sroot = Get-YamlScalar -Path $srcCfg -Key "secrets_root"
    if ($sroot) { $tried.Add("sources/$id.yaml 的 secrets_root = $sroot") }
}
if (-not $sroot) {
    $sroot = Get-YamlScalar -Path $SettingsPath -Key "secrets_root"
    if ($sroot) { $tried.Add("settings.yaml 的 secrets_root = $sroot") }
}
if (-not $sroot) { $sroot = Join-Path $SkillRoot ".km" }
$sroot = $sroot.TrimEnd('\', '/')

$order = New-Object System.Collections.Generic.List[object]
if ($SecretDir)    { $order.Add(@{ dir = $SecretDir; why = "显式 -SecretDir" }) }
if ($DataRoot)     { $order.Add(@{ dir = $DataRoot;  why = "兼容参数 -DataRoot（含义=秘密材料目录）" }) }
if ($env:KM_SECRET_DIR) { $order.Add(@{ dir = $env:KM_SECRET_DIR; why = "环境变量 KM_SECRET_DIR" }) }
if ($id)           { $order.Add(@{ dir = (Join-Path $sroot $id); why = "秘密材料根($sroot) + 源 id($id)" }) }
if (Test-Path -LiteralPath $sroot) {
    foreach ($d in (Get-ChildItem -LiteralPath $sroot -Directory -ErrorAction SilentlyContinue)) {
        if (Test-SecretDir -Dir $d.FullName) {
            $order.Add(@{ dir = $d.FullName; why = "秘密材料根($sroot) 下含 vault\map 的目录：$($d.Name)" })
        }
    }
}

foreach ($c in $order) {
    if (Test-SecretDir -Dir $c.dir) { $resolvedDir = $c.dir; $resolvedWhy = $c.why; break }
    # 传进来的可能是「数据根」：其下 <id> 子目录才是工作副本/秘密材料
    if ($id -and (Test-SecretDir -Dir (Join-Path $c.dir $id))) {
        $resolvedDir = (Join-Path $c.dir $id)
        $resolvedWhy = "$($c.why) → 其下 <id> 子目录"
        break
    }
    $tried.Add("跳过（无 vault\map\）：$($c.dir)  [$($c.why)]")
}

if (-not $resolvedDir) {
    Write-Host "`n[无法定位秘密材料目录]" -ForegroundColor Red
    Write-Host "  本脚本要的是「秘密材料目录」= 含 vault\map\ 的那一级，通常为" -ForegroundColor Yellow
    Write-Host "    <秘密材料根>\<源id>      （缺省 <技能目录>\.km\<源id>）" -ForegroundColor Yellow
    Write-Host "  它不是真源，也不是工作副本。" -ForegroundColor Yellow
    Write-Host "`n  已尝试：" -ForegroundColor Yellow
    $tried | ForEach-Object { Write-Host "    - $_" -ForegroundColor DarkGray }
    Write-Host "`n  请明确指定，例如：" -ForegroundColor Yellow
    Write-Host "    pwsh -File `"$PSCommandPath`" -SecretsRoot `"<秘密材料根>`" -Id `"<源id>`"" -ForegroundColor Yellow
    Write-Host "    pwsh -File `"$PSCommandPath`" -SecretDir `"<秘密材料目录>`"" -ForegroundColor Yellow
    return
}
$resolvedDir = (Resolve-Path -LiteralPath $resolvedDir).Path
$MapDir = Join-Path $resolvedDir "vault\map"

# ---------------------------------------------------------------------------
# 三个目录对照：彻底消除「这是原仓库还是工作目录」的歧义
# ---------------------------------------------------------------------------
Write-Host "`n=== 目录对照 ===" -ForegroundColor Cyan
Write-Host "  秘密材料目录（本脚本用）: $resolvedDir" -ForegroundColor Green
Write-Host "  解析依据                : $resolvedWhy" -ForegroundColor Green
$wsPath = Join-Path $resolvedDir "workspace.json"
if (Test-Path -LiteralPath $wsPath) {
    try {
        $ws = Get-Content -LiteralPath $wsPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($ws.source) { Write-Host "  真源(只读)              : $($ws.source)" -ForegroundColor DarkGray }
        if ($ws.mirror) { Write-Host "  工作副本(已脱敏可上云)  : $($ws.mirror)" -ForegroundColor DarkGray }
    } catch { }
}
Write-Host "  说明：真源与工作副本都**不含** vault\map\；只有秘密材料目录含（所以它才是本参数）。" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# bw 可用性 / 导入文件 / 登录态预检
# ---------------------------------------------------------------------------
$Bw = $BwPath
$bwOk = $true
if (-not (Get-Command $Bw -ErrorAction SilentlyContinue)) {
    if (Test-Path -LiteralPath $Bw) { $Bw = (Resolve-Path -LiteralPath $Bw).Path }
    else { $bwOk = $false }
}
if (-not $bwOk) {
    if ($DryRun) {
        Write-Host "`n[警告] 未找到 bw（$BwPath）：-DryRun 只做目录/文件预览，跳过 vault 预检。" -ForegroundColor Yellow
    } else {
        throw "找不到 bw：$BwPath（用 -BwPath 指定绝对路径，或 scoop install bitwarden-cli）"
    }
}

if ($File) {
    if (-not (Test-Path -LiteralPath $File)) { throw "指定的导入文件不存在：$File" }
    $importFile = Get-Item -LiteralPath $File
}
else {
    $importFile = Get-ChildItem -LiteralPath $MapDir -Filter "bw-import-*.json" -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime | Select-Object -Last 1
}
if (-not $importFile) { throw "未找到 bw-import-*.json（$MapDir），请先运行 pipeline.py / sanitize.py --apply" }

Write-Host "`n=== 导入文件（自动取最新）===" -ForegroundColor Cyan
Write-Host "  $($importFile.FullName)"
$payload = Get-Content -LiteralPath $importFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
$nItems = if ($null -ne $payload.items) { @($payload.items).Count } else { 0 }
Write-Host "  条目数：$nItems" -ForegroundColor Cyan
if ($nItems -eq 0) {
    Write-Host "  提示：这份载荷是空的（本次没有新的 credential 命中），无需导入。" -ForegroundColor Yellow
}

if ($bwOk) {
    Write-Host "`n=== 当前登录状态 ===" -ForegroundColor Cyan
    $statusObj = & $Bw status | ConvertFrom-Json
    Write-Host "  status    = $($statusObj.status)"
    Write-Host "  serverUrl = $($statusObj.serverUrl)"

    if ($statusObj.status -eq "unauthenticated") {
        Write-Host "`n[未登录] 会话无效，请先重新登录后重跑本脚本：" -ForegroundColor Red
        $loginHint = if ($Account) { "bw logout; if ($?) { bw login $Account }" }
                     else { "bw logout; bw login <你的邮箱>" }
        Write-Host "    $loginHint" -ForegroundColor Yellow
        return
    }
}

if ($DryRun) {
    Write-Host "`n[-DryRun] 仅预览（未解锁、未导入）。" -ForegroundColor Yellow
    Write-Host "  正式导入（在你自己终端，需输入主密码）：" -ForegroundColor Yellow
    Write-Host "    pwsh -File `"$PSCommandPath`" -SecretDir `"$resolvedDir`"" -ForegroundColor Yellow
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
        Write-Host "         `$env:BW_SESSION = (bw unlock --raw) ; pwsh -File `"$PSCommandPath`" -SecretDir `"$resolvedDir`"" -ForegroundColor Yellow
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
        $items | Out-File -LiteralPath $itemsFile -Encoding utf8
        Write-Host "  已写入：$itemsFile（$imported 条）" -ForegroundColor Green
        Write-Host "  下一步（回填「见保险库：xxx」）：" -ForegroundColor Yellow
        Write-Host "    python scripts\backfill.py --data-root `"$resolvedDir`" --apply" -ForegroundColor Yellow
        Write-Host "    （backfill 的 --data-root 同样指**秘密材料目录**）" -ForegroundColor DarkGray
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