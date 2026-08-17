$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "VoiceContext is not installed. Run .\install.ps1 first."
}

& $Python (Join-Path $Root "tools\generate_assets.py")
& $Python -m PyInstaller --noconfirm --clean (Join-Path $Root "VoiceContext-onefile.spec")

Write-Host "One-file build complete: $Root\dist\VoiceContext.exe"
