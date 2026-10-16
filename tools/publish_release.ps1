#Requires -Version 5.1
# ============================================================================
# 重要：本文件必须保存为 **UTF-8 with BOM**。
# Windows PowerShell 5.1 读取没有 BOM 的 .ps1 时会按系统 ANSI 码页解释，
# 中文会变成乱码并导致「字符串未闭合」的 ParserError。
# ============================================================================
<#
.SYNOPSIS
    把打包产物作为 GitHub Release 附件发布。

.DESCRIPTION
    为什么要有这个脚本：可执行程序 81 MB，不适合提交进 git 版本库
    （每发一版都会在历史里永久留下一个 81 MB 对象）。GitHub Releases 的
    附件是仓库外的独立存储，还能带发布说明，是分发二进制该走的路。

    脚本流程：
      1. 从 src/everyday/__init__.py 读取版本号（可用 -Tag 覆盖）
      2. 检查 release/EveryDay.exe 是否存在（没有就提示先跑 build_exe.ps1）
      3. 读取 docs/release-notes/<tag>.md 作为发布说明
      4. 确认 tag 存在并推送到远端
      5. 创建 Release（已存在则复用），上传 exe 作为附件
         同名附件会先删除再上传，便于「重新发布同一版本」

    认证方式（二选一）：
      * 环境变量 GH_TOKEN 或 GITHUB_TOKEN（推荐）
        需要 fine-grained PAT 或 classic PAT，权限：Contents = Read and write
        设置方法（当前会话）：
            $env:GH_TOKEN = "github_pat_xxx"
        永久设置：
            [Environment]::SetEnvironmentVariable('GH_TOKEN','github_pat_xxx','User')
      * 已安装并登录的 gh CLI（脚本会自动回退到它）

    ⚠️ 不要把 token 写进本文件或任何会被提交的文件。

.PARAMETER Tag
    发布用的 tag，默认取包版本号加 v 前缀（例如 v1.0.0）。

.PARAMETER Artifact
    要上传的产物路径，默认 release/EveryDay.exe。

.PARAMETER NotesFile
    发布说明文件，默认 docs/release-notes/<tag>.md。

.PARAMETER Draft
    创建为草稿（不公开，适合先检查再发布）。

.PARAMETER Prerelease
    标记为预发布。

.PARAMETER SkipTagPush
    不推送 tag（tag 已在远端时用）。

.EXAMPLE
    $env:GH_TOKEN = "github_pat_xxx"
    powershell -ExecutionPolicy Bypass -File tools\publish_release.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\publish_release.ps1 -Tag v1.0.1 -Draft
#>
[CmdletBinding()]
param(
    [string]$Tag,
    [string]$Artifact,
    [string]$NotesFile,
    [switch]$Draft,
    [switch]$Prerelease,
    [switch]$SkipTagPush
)

$ErrorActionPreference = 'Stop'

# PS 5.1 默认可能不带 TLS 1.2，调 GitHub API 会直接失败
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$git = 'git'

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    $output = & $git -C $root @Args 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Args -join ' ') 失败：`n$($output -join "`n")"
    }
    return $output
}

# ---- 1. 版本号 / tag ------------------------------------------------------
if (-not $Tag) {
    $initFile = Join-Path $root 'src\everyday\__init__.py'
    if (-not (Test-Path $initFile)) { throw "找不到版本文件：$initFile" }
    $match = Select-String -Path $initFile -Pattern '__version__\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if (-not $match) { throw "无法从 $initFile 解析 __version__，请用 -Tag 指定。" }
    $Tag = 'v' + $match.Matches[0].Groups[1].Value
}
Write-Host "发布版本：$Tag" -ForegroundColor Cyan

# ---- 2. 产物 --------------------------------------------------------------
if (-not $Artifact) { $Artifact = Join-Path $root 'release\EveryDay.exe' }
if (-not (Test-Path $Artifact)) {
    throw "找不到产物：$Artifact`n请先打包：powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1"
}
$artifactItem = Get-Item $Artifact
$artifactName = $artifactItem.Name
Write-Host ("产物：{0}（{1:N1} MB）" -f $artifactName, ($artifactItem.Length / 1MB))

# ---- 3. 发布说明 ----------------------------------------------------------
if (-not $NotesFile) { $NotesFile = Join-Path $root "docs\release-notes\$Tag.md" }
$notes = ''
if (Test-Path $NotesFile) {
    $notes = Get-Content $NotesFile -Raw -Encoding UTF8
    Write-Host "发布说明：$NotesFile"
} else {
    $notes = "EveryDay $Tag"
    Write-Warning "未找到发布说明文件 $NotesFile，将使用默认标题。"
}

# ---- 4. 远端仓库 ----------------------------------------------------------
$remoteUrl = (Invoke-Git remote get-url origin) -join ''
$remoteUrl = $remoteUrl.Trim()
$slug = $null
if ($remoteUrl -match 'github\.com[:/](?<owner>[^/]+)/(?<repo>[^/]+?)(\.git)?$') {
    $slug = "$($Matches['owner'])/$($Matches['repo'])"
}
if (-not $slug) { throw "无法从 origin 解析出 owner/repo：$remoteUrl" }
Write-Host "目标仓库：$slug"

# ---- 5. tag ---------------------------------------------------------------
$existingTags = (Invoke-Git tag --list $Tag) -join ''
if (-not $existingTags.Trim()) {
    Invoke-Git tag -a $Tag -m "EveryDay $Tag" | Out-Null
    Write-Host "已创建 tag：$Tag"
}
if (-not $SkipTagPush) {
    Invoke-Git push origin $Tag | Out-Null
    Write-Host "已推送 tag：$Tag"
}

# ---- 6. 认证 --------------------------------------------------------------
$token = $env:GH_TOKEN
if (-not $token) { $token = $env:GITHUB_TOKEN }
$useGh = $false
if (-not $token) {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        & gh auth status 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $useGh = $true }
    }
}
if (-not $token -and -not $useGh) {
    throw @"
没有可用的 GitHub 凭据，无法创建 Release。

请二选一：
  A) 设置个人访问令牌（推荐，需要 Contents: Read and write 权限）
       `$env:GH_TOKEN = "github_pat_xxx"
     然后重新运行本脚本
  B) 安装并登录 gh CLI：gh auth login

或者完全不用本脚本：到 https://github.com/$slug/releases/new
选择 tag $Tag，把 $artifactName 拖进去即可。
"@
}

$headers = @{
    'Accept'               = 'application/vnd.github+json'
    'User-Agent'           = 'EveryDay-Release-Script'
    'X-GitHub-Api-Version' = '2022-11-28'
}
if ($token) { $headers['Authorization'] = "Bearer $token" }
$api = "https://api.github.com/repos/$slug"

# ---- 7. 创建或复用 Release ------------------------------------------------
$release = $null
try {
    $release = Invoke-RestMethod -Uri "$api/releases/tags/$Tag" -Headers $headers -Method Get
    Write-Host "Release $Tag 已存在，将复用它并覆盖同名附件。" -ForegroundColor Yellow
} catch {
    $body = @{
        tag_name   = $Tag
        name       = "EveryDay $Tag"
        body       = $notes
        draft      = [bool]$Draft
        prerelease = [bool]$Prerelease
    } | ConvertTo-Json -Depth 5
    $release = Invoke-RestMethod -Uri "$api/releases" -Headers $headers -Method Post `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -ContentType 'application/json; charset=utf-8'
    Write-Host "已创建 Release：$($release.html_url)" -ForegroundColor Green
}

if (-not $release) { throw "Release 创建或获取失败。" }

# ---- 8. 上传附件（同名先删） ---------------------------------------------
$assets = Invoke-RestMethod -Uri "$api/releases/$($release.id)/assets" -Headers $headers -Method Get
foreach ($asset in $assets) {
    if ($asset.name -eq $artifactName) {
        Write-Host "删除同名旧附件：$artifactName"
        Invoke-RestMethod -Uri "$api/releases/assets/$($asset.id)" -Headers $headers -Method Delete | Out-Null
    }
}

# upload_url 形如 https://uploads.github.com/...{?name,label}
$uploadUrl = $release.upload_url -replace '\{\?[^}]*\}', ''
$uploadUri = "$uploadUrl`?name=$([uri]::EscapeDataString($artifactName))"

Write-Host "正在上传 $artifactName（约 $([math]::Round($artifactItem.Length/1MB,1)) MB）..." -ForegroundColor Cyan
$uploaded = Invoke-RestMethod -Uri $uploadUri -Headers $headers -Method Post `
    -InFile $artifactItem.FullName -ContentType 'application/octet-stream'

Write-Host ""
Write-Host "发布完成！" -ForegroundColor Green
Write-Host "  Release 页面：$($release.html_url)"
Write-Host "  直链下载    ：$($uploaded.browser_download_url)"
Write-Host ""
Write-Host "把直链贴到 README 里即可让用户直接下载。" -ForegroundColor DarkGray
