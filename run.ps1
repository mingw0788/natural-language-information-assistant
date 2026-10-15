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
if (-not (Test-Path $VenvPython)) {
    & $PythonCommand.Source -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败。" }
}

& $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "安装锁定依赖失败；请检查网络后重新运行。" }

& $VenvPython -m pip install --disable-pip-version-check --no-build-isolation --no-deps -e $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败；请检查网络后重新运行。" }

$BrowserCheck = 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); b.close(); p.stop()'
& $VenvPython -c $BrowserCheck *> $null
if ($LASTEXITCODE -ne 0) {
    $EdgeBrowserCheck = 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True, channel="msedge"); b.close(); p.stop()'
    & $VenvPython -c $EdgeBrowserCheck *> $null
    if ($LASTEXITCODE -eq 0) {
        $env:INFO_ASSISTANT_BROWSER_CHANNEL = "msedge"
        Write-Host "未找到 Playwright Chromium，将使用本机 Microsoft Edge 读取动态页面。"
    } else {
        Write-Host "首次运行需要安装动态页面读取组件 Chromium（约 200 MiB）。"
        & $VenvPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Chromium 安装失败；应用仍会启动，动态页面将回退到静态正文。可在网络可用后再次运行脚本。"
        } else {
            & $VenvPython -c $BrowserCheck *> $null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Chromium 安装后仍无法启动；应用仍会启动，动态页面将回退到静态正文。"
            }
        }
    }
}

Write-Host "服务仅监听本机：http://127.0.0.1:8000"
Write-Host "外部搜索和模型服务不会自动启用；如需联网搜索，请先在设置页面配置对应服务。"
Write-Host "停止服务请按 Ctrl+C。"
& $VenvPython -m uvicorn info_assistant.main:app --host 127.0.0.1 --port 8000
