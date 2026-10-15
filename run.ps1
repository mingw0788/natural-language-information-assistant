$ErrorActionPreference = "Stop"

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

& $VenvPython -m pip install --disable-pip-version-check -e "$($PSScriptRoot)[dev]"
if ($LASTEXITCODE -ne 0) { throw "安装依赖失败；请检查网络后重新运行。" }

Write-Host "服务仅监听本机：http://127.0.0.1:8000"
Write-Host "停止服务请按 Ctrl+C。"
& $VenvPython -m uvicorn info_assistant.main:app --host 127.0.0.1 --port 8000
