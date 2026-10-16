$ErrorActionPreference = "Stop"
$env:INFO_ASSISTANT_BROWSER_CHANNEL = ""

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $PythonCommand) {
    throw "未找到 Python。请安装 Python 3.11 后重新运行此脚本。"
}

$PythonVersion = & $PythonCommand.Source --version 2>&1
if ($PythonVersion -notmatch "Python 3\.11\.") {
    throw "需要 Python 3.11，当前检测到：$PythonVersion"
}

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

function Invoke-QuietPython {
    param([string[]]$ArgumentList)

    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $VenvPython @ArgumentList *> $null
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
}

if (-not (Test-Path $VenvPython)) {
    & $PythonCommand.Source -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "安装锁定依赖失败；请检查网络后重新运行。" }

& $VenvPython -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败；请检查网络后重新运行。" }

$BrowserCheck = 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()'
$BrowserCheckExitCode = Invoke-QuietPython -ArgumentList @("-c", $BrowserCheck)
if ($BrowserCheckExitCode -ne 0) {
    $EdgeBrowserCheck = 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, channel="msedge"); b.close(); p.stop()'
    $EdgeBrowserCheckExitCode = Invoke-QuietPython -ArgumentList @("-c", $EdgeBrowserCheck)
    if ($EdgeBrowserCheckExitCode -eq 0) {
        $env:INFO_ASSISTANT_BROWSER_CHANNEL = "msedge"
        Write-Host "未找到 Playwright Chromium，将使用本机 Microsoft Edge 读取动态页面。"
    } else {
        Write-Host "未找到可用的 Chromium 或 Microsoft Edge，将直接启动并读取静态页面。"
        Write-Host "需要动态页面时，可自行选择安装 Chromium（约 200 MiB），本脚本不会自动下载："
        Write-Host ('& "{0}" -m playwright install chromium' -f $VenvPython)
    }
}

Write-Host "服务仅监听本机：http://127.0.0.1:8000"
Write-Host "默认无需 API 密钥即可搜索公开网页，搜索词会发送到所选搜索网站。"
Write-Host "默认在本机读取来源，不启用云模型；搜索网站与可选服务可在设置页面调整。"
Write-Host "停止服务请按 Ctrl+C。"
& $VenvPython -m uvicorn info_assistant.main:app --host 127.0.0.1 --port 8000
