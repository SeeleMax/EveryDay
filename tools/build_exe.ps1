#Requires -Version 5.1
# ============================================================================
# 重要：本文件必须保存为 **UTF-8 with BOM**。
# Windows PowerShell 5.1 读取没有 BOM 的 .ps1 时会按系统 ANSI 码页解释，
# 文件里的中文会被解码成乱码，进而导致「字符串未闭合」的 ParserError
# （报错是 TerminatorExpectedAtEndOfString，跟实际内容完全对不上）。
# 用编辑器另存时请确认编码为 UTF-8 BOM。
# ============================================================================
<#
.SYNOPSIS
    把 EveryDay 打包成可直接运行的可执行文件，并自检产物能否真正启动。

.DESCRIPTION
    步骤：环境检查 -> 净化 PATH -> 生成图标 -> PyInstaller -> 产物自检 -> 输出到 release/。

    产物有两种模式：
      * 默认（单文件）：release/EveryDay.exe —— 双击即用，自包含，约 81 MB。
        首次启动会把自己解包到临时目录，所以启动慢 1~3 秒。
      * -Onedir（目录模式）：release/EveryDay/ 整个目录，约 168 MB。
        启动更快，但分发时要带上整个目录。

    打包流程里有六处是**踩过坑才定下来的**，改动前请先读注释：

    1. 净化 PATH（见下面 $buildPath）
       PyInstaller 会顺着 PATH 去找被依赖的 DLL。开发机上若装了 PostgreSQL、
       Anaconda 之类自带 OpenSSL/ICU 的软件，就会把它们的 DLL 一起收进产物：
         LIBPQ.dll、libcrypto-3-x64.dll、libssl-3-x64.dll、
         icudt73.dll、icuuc.dll、krb5_64.dll、gssapi64.dll、comerr64.dll
       其中旧版 icuuc.dll 会抢占 Qt 的 ICU 绑定，运行时直接报
         ImportError: DLL load failed while importing QtCore: 找不到指定的程序
       （ERROR_PROC_NOT_FOUND）。而且这个污染是**时有时无**的，
       所以必须靠净化 PATH + 末尾的产物自检来兜住。

    2. --paths src
       本项目是 src 布局，main.py 只在运行时把 src 插进 sys.path。
       PyInstaller 的静态分析不执行那段代码，不加这个参数就找不到
       everyday 包，产物只有 7.9 MB 且静默退出。

    3. --collect-binaries PySide6 --collect-data PySide6
       PyInstaller 自带的 PySide6 钩子漏收了必要文件，不加就是同一个
       QtCore DLL 错误。只加 --collect-binaries 在单文件模式下仍会失败。

    4. --add-data 用绝对路径
       下面指定了 --specpath，PyInstaller 会把相对的源路径按 spec 文件所在
       目录（build/）解析，写成相对路径会找不到 build\migrations 而报错。

    5. migrations / config 必须随包分发
       打包后 everyday.paths 会在 sys._MEIPASS（单文件）或 exe 同级（目录）
       里找 migrations/001_initial.sql，找不到就无法建库。

    6. 产物自检
       打包成功 ≠ 能运行。这里会真的把产物启动一次，用 --data-dir 指向临时
       目录，等日志出现「初始化完成，进入事件循环」才算通过。
       PyInstaller 退出码为 0 但产物跑不起来是常态，没有这一步会交付坏包。

.PARAMETER Onedir
    使用目录模式而不是单文件模式。

.PARAMETER Clean
    打包前删除 build/ 与 release/ 中的旧产物。

.PARAMETER SkipVerify
    跳过产物自检（不推荐，仅在你没有图形会话时使用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
    powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1 -Onedir -Clean
#>
[CmdletBinding()]
param(
    [switch]$Onedir,
    [switch]$Clean,
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = 'python'
    Write-Warning "未找到 .venv\Scripts\python.exe，改用 PATH 上的 python。建议先建好虚拟环境。"
}

# ---- 环境检查 -------------------------------------------------------------
& $python -c "import PySide6" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "缺少 PySide6。请先执行：`n  $python -m pip install -r requirements.txt"
}
& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "缺少 PyInstaller。请先执行：`n  $python -m pip install pyinstaller`n国内网络建议加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple"
}

# ---- 净化 PATH（见文件头说明 1） ------------------------------------------
$originalPath = $env:PATH
$buildPath = @(
    (Join-Path $env:SystemRoot 'system32'),
    $env:SystemRoot,
    (Join-Path $env:SystemRoot 'System32\Wbem'),
    (Join-Path $root '.venv\Scripts')
) -join ';'
$env:PATH = $buildPath
Write-Host "已净化 PATH（仅保留系统目录与 venv），避免混入第三方 OpenSSL/ICU" -ForegroundColor DarkGray

# ---- 清理 -----------------------------------------------------------------
if ($Clean) {
    foreach ($item in @(
        (Join-Path $root 'build'),
        (Join-Path $root 'release'),
        (Join-Path $root 'EveryDay.spec')
    )) {
        if (Test-Path $item) { Remove-Item $item -Recurse -Force }
    }
}

try {
    # ---- 图标（现场生成，仓库里不存二进制） -------------------------------
    $icon = Join-Path $root 'build\everyday.ico'
    & $python (Join-Path $root 'tools\make_icon.py') $icon
    if (-not (Test-Path $icon)) {
        Write-Warning "图标生成失败，将使用 PyInstaller 默认图标。"
        $icon = $null
    }

    # ---- 参数 -------------------------------------------------------------
    $modeArgs = if ($Onedir) { @('--onedir') } else { @('--onefile') }
    $iconArgs = if ($icon) { @('--icon', $icon) } else { @() }
    $pathArgs = @('--paths', (Join-Path $root 'src'))
    $collectArgs = @('--collect-binaries', 'PySide6', '--collect-data', 'PySide6')
    $dataArgs = @(
        '--add-data', ((Join-Path $root 'migrations') + ';migrations'),
        '--add-data', ((Join-Path $root 'config') + ';config')
    )

    $arguments = @(
        '-m', 'PyInstaller',
        '--noconfirm',
        '--clean',
        '--noconsole',
        '--name', 'EveryDay',
        '--distpath', (Join-Path $root 'release'),
        '--workpath', (Join-Path $root 'build\pyinstaller'),
        '--specpath', (Join-Path $root 'build')
    ) + $modeArgs + $iconArgs + $pathArgs + $collectArgs + $dataArgs + @(
        # 只排除确定用不到、且与本项目无关的模块。
        '--exclude-module', 'tkinter',
        '--exclude-module', 'unittest',
        '--exclude-module', 'pytest',
        (Join-Path $root 'main.py')
    )

    Write-Host "开始打包（$(if ($Onedir) { '目录模式' } else { '单文件模式' })）..." -ForegroundColor Cyan
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，退出码 $LASTEXITCODE" }

    # ---- 结果 -------------------------------------------------------------
    $artifact = if ($Onedir) {
        Join-Path $root 'release\EveryDay'
    } else {
        Join-Path $root 'release\EveryDay.exe'
    }
    if (-not (Test-Path $artifact)) { throw "打包似乎成功，但找不到产物：$artifact" }

    $item = Get-Item $artifact
    if ($item.PSIsContainer) {
        $size = (Get-ChildItem $artifact -Recurse -File | Measure-Object Length -Sum).Sum
        Write-Host "打包完成：$artifact（目录，合计 $([math]::Round($size/1MB,1)) MB）" -ForegroundColor Green
    } else {
        Write-Host "打包完成：$artifact（$([math]::Round($item.Length/1MB,1)) MB）" -ForegroundColor Green
    }

    # ---- 产物自检（见文件头说明 6） ---------------------------------------
    if ($SkipVerify) {
        Write-Warning "已跳过产物自检（-SkipVerify）。产物未必能启动。"
    } else {
        $runner = if ($item.PSIsContainer) { Join-Path $artifact 'EveryDay.exe' } else { $artifact }
        $verifyData = Join-Path ([System.IO.Path]::GetTempPath()) ("everyday-verify-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
        $verifyLog = Join-Path $verifyData 'logs\everyday.log'

        Write-Host "正在自检产物能否启动..." -ForegroundColor Cyan
        $proc = Start-Process -FilePath $runner -ArgumentList '--data-dir', $verifyData -PassThru
        $deadline = (Get-Date).AddSeconds(60)
        $started = $false
        $crashed = $false
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            if (Test-Path $verifyLog) {
                if (Select-String -Path $verifyLog -Pattern '进入事件循环' -Quiet -ErrorAction SilentlyContinue) {
                    $started = $true
                    break
                }
                if (Select-String -Path $verifyLog -Pattern 'Traceback' -Quiet -ErrorAction SilentlyContinue) {
                    $crashed = $true
                    break
                }
            }
            if ($proc.HasExited) { break }
        }
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
        if (Test-Path $verifyData) { Remove-Item $verifyData -Recurse -Force -ErrorAction SilentlyContinue }

        if ($started) {
            Write-Host "自检通过：产物成功启动并进入事件循环。" -ForegroundColor Green
        } else {
            $detail = if (Test-Path $verifyLog) { (Get-Content $verifyLog -Tail 6) -join "`n" } else { '（没有生成日志，可能根本没跑起来）' }
            Write-Host "自检失败：产物未能启动。日志尾部：" -ForegroundColor Red
            Write-Host $detail -ForegroundColor DarkGray
            if ($crashed) {
                throw "产物自检失败。常见原因是 PATH 污染了第三方 OpenSSL/ICU（见本脚本文件头说明 1）。"
            }
            throw "产物自检失败（超时或进程提前退出）。"
        }
    }

    Write-Host ""
    Write-Host "试运行：" -ForegroundColor Cyan
    Write-Host "  & '$artifact'            # 正常启动，数据写入 %LOCALAPPDATA%\EveryDay"
}
finally {
    $env:PATH = $originalPath
}
