from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from PySide6.QtGui import QClipboard


class OutputDispatcher:
    def __init__(self, clipboard: QClipboard) -> None:
        self.clipboard = clipboard

    def copy(self, text: str) -> None:
        self.clipboard.setText(text, QClipboard.Mode.Clipboard)

    def paste(self, submit: bool = False) -> None:
        if os.name == "nt":
            script = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v');"
            )
            if submit:
                script += " Start-Sleep -Milliseconds 80; [System.Windows.Forms.SendKeys]::SendWait('{ENTER}');"
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return
        try:
            import keyboard

            keyboard.send("ctrl+v")
            if submit:
                time.sleep(0.08)
                keyboard.send("enter")
        except ImportError as exc:
            raise RuntimeError("Automatic paste is unavailable on this platform.") from exc

    @staticmethod
    def export_markdown(text: str, directory: str, session_id: str) -> Path:
        target_dir = Path(directory).expanduser() if directory else Path.home() / "Documents" / "VoiceContext"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"voicecontext-{session_id[:8]}.md"
        path.write_text(text, encoding="utf-8")
        return path
