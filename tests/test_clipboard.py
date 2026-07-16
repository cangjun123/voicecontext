from __future__ import annotations

from PySide6.QtCore import QMimeData

from voice_context.input.clipboard_watcher import ClipboardWatcher


def test_html_clipboard_is_converted_to_plain_text(qtbot, qapp) -> None:
    watcher = ClipboardWatcher(
        qapp.clipboard(),
        {
            "capture_during_session": True,
            "capture_text": False,
            "capture_html": True,
            "capture_files": False,
            "capture_urls": False,
            "max_chars": 20000,
        },
    )
    watcher.start()
    data = QMimeData()
    data.setHtml("<p>Hello <strong>world</strong></p>")
    with qtbot.waitSignal(watcher.captured, timeout=1000) as signal:
        qapp.clipboard().setMimeData(data)
    assert signal.args[0] == "Hello world"
