$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    py -3.12 -m venv (Join-Path $Root ".venv")
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -e "${Root}[dev]"

Write-Host "VoiceContext is installed. Run .\run.ps1 or .\.venv\Scripts\voicecontext.exe"
