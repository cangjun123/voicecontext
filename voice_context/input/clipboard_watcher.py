from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QClipboard
from PySide6.QtGui import QTextDocument

from .foreground import get_foreground_info


SENSITIVE_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?\S{8,}"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
)


class ClipboardWatcher(QObject):
    captured = Signal(str, str, str, str, bool)

    def __init__(self, clipboard: QClipboard, config: dict[str, Any]) -> None:
        super().__init__()
        self.clipboard = clipboard
        self.config = config
        self.enabled = False
        self._suppress_once = False
        self._last_text = ""
        clipboard.dataChanged.connect(self._on_change)

    def start(self) -> None:
        self.enabled = True
        self._suppress_once = False
        self._last_text = self.clipboard.text().strip()

    def stop(self) -> None:
        self.enabled = False

    def suppress_next_change(self) -> None:
        self._suppress_once = True

    def _on_change(self) -> None:
        if self._suppress_once:
            self._suppress_once = False
            return
        if not self.enabled or not self.config.get("capture_during_session", True):
            return
        mime = self.clipboard.mimeData()
        if mime is None:
            return
        content_type = "text"
        text = ""
        if mime.hasUrls() and self.config.get("capture_files", True):
            urls = mime.urls()
            local_files = [url.toLocalFile() for url in urls if url.isLocalFile()]
            if local_files:
                text = "\n".join(local_files)
                content_type = "file"
            elif self.config.get("capture_urls", True):
                text = "\n".join(url.toString() for url in urls)
                content_type = "url"
        if not text and mime.hasHtml() and self.config.get("capture_html", True):
            document = QTextDocument()
            document.setHtml(mime.html())
            text = document.toPlainText()
        if not text and mime.hasText() and self.config.get("capture_text", True):
            text = mime.text()
        text = text.strip()
        if not text or text == self._last_text:
            return
        self._last_text = text
        maximum = max(1, int(self.config.get("max_chars", 20000)))
        text = text[:maximum]
        sensitive = any(pattern.search(text) for pattern in SENSITIVE_PATTERNS)
        foreground = get_foreground_info()
        self.captured.emit(text, content_type, foreground.process, foreground.title, sensitive)
