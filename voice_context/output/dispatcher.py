from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QClipboard


@dataclass(slots=True)
class ClipboardWriteResult:
    success: bool
    attempts: int
    error: str = ""


class OutputDispatcher:
    RETRY_DELAYS_MS = (100, 200)

    def __init__(self, clipboard: QClipboard) -> None:
        self.clipboard = clipboard
        self._copy_generation = 0

    def copy_verified(
        self,
        text: str,
        callback: Callable[[ClipboardWriteResult], None],
    ) -> None:
        self._copy_generation += 1
        self._copy_attempt(text, callback, 1, self._copy_generation)

    def _copy_attempt(
        self,
        text: str,
        callback: Callable[[ClipboardWriteResult], None],
        attempt: int,
        generation: int,
    ) -> None:
        if generation != self._copy_generation:
            return
        try:
            self.clipboard.setText(text, QClipboard.Mode.Clipboard)
            if self.clipboard.text(QClipboard.Mode.Clipboard) == text:
                callback(ClipboardWriteResult(True, attempt))
                return
            error = "Clipboard read-back did not match the generated prompt."
        except Exception as exc:
            error = str(exc)
        if attempt > len(self.RETRY_DELAYS_MS):
            callback(ClipboardWriteResult(False, attempt, error))
            return
        delay = self.RETRY_DELAYS_MS[attempt - 1]
        QTimer.singleShot(
            delay,
            lambda: self._copy_attempt(text, callback, attempt + 1, generation),
        )

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
