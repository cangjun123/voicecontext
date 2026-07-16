$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "VoiceContext is not installed. Run .\install.ps1 first."
}

Start-Process -FilePath $Pythonw -ArgumentList "-m", "voice_context" -WorkingDirectory $Root -WindowStyle Hidden
