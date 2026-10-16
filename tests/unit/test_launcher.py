import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_launcher_parses_in_windows_powershell() -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        pytest.skip("Windows PowerShell is not installed")

    launcher_path = Path(__file__).resolve().parents[2] / "run.ps1"
    environment = os.environ.copy()
    environment["NLIA_LAUNCHER_PATH"] = str(launcher_path)
    command = (
        "$tokens = $null; $parseErrors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$env:NLIA_LAUNCHER_PATH, [ref]$tokens, [ref]$parseErrors) | Out-Null; "
        "if ($parseErrors.Count -gt 0) { exit 1 }"
    )

    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        check=False,
        encoding="utf-8",
        env=environment,
        errors="replace",
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
