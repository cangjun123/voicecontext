from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from voice_context.core.session import SessionState, VoiceSession
from voice_context.input.hotkeys import HotkeyManager
from voice_context.storage.config_store import ConfigStore
from voice_context.ui.hotkey_capture import HotkeyCaptureEdit
from voice_context.ui.main_window import MainWindow
from voice_context.ui.overlay import OverlayWindow
from voice_context.ui.i18n import set_language
from voice_context.ui.settings_window import SettingsWindow
from voice_context.ui.tray import TrayIcon


class MemorySecrets:
    def __init__(self) -> None:
        self.values = {}

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


def test_main_window_renders_session(qtbot) -> None:
    set_language("en")
    window = MainWindow()
    qtbot.addWidget(window)
    session = VoiceSession()
    session.transition(SessionState.LISTENING)
    session.add_speech("test speech", 0, 100)
    session.add_reference("Error: test", content_type="error")
    window.set_session(session)
    assert window.transcript_edit.toPlainText() == "test speech"
    assert window.ref_count.text() == "1 captured"
    assert window.stop_button.isVisible() is False  # Parent window is not shown in the test.
    assert window.stop_button.isHidden() is False


def test_main_window_shows_actual_llm_model_and_fallback(qtbot) -> None:
    set_language("en")
    window = MainWindow()
    qtbot.addWidget(window)

    window.set_composer_status(
        {
            "requested": True,
            "used": True,
            "model": "configured-model",
            "response_model": "actual-model",
            "latency_ms": 321,
            "usage": {"prompt_tokens": 120, "completion_tokens": 34},
            "streamed": True,
        }
    )
    assert window.context_label.text() == (
        "Prompt ready via streaming LLM: actual-model (321 ms) · Tokens: 120 in / 34 out"
    )

    window.set_composer_status({"requested": True, "used": False}, "timed out")
    assert window.context_label.text() == "LLM failed"
    assert window.context_label.toolTip() == "timed out"


def test_main_window_shows_full_completion_receipt(qtbot) -> None:
    set_language("en")
    window = MainWindow()
    qtbot.addWidget(window)
    session = VoiceSession()
    session.asr_metadata = {"status": "success", "model": "fun-asr-realtime"}
    session.composer_metadata = {
        "requested": True,
        "used": True,
        "response_model": "deepseek",
        "latency_ms": 250,
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }
    session.output_metadata = {"status": "copied"}
    session.add_reference("A")

    window.set_completion_receipt(session)

    assert window.copy_button.text() == "Save and copy"
    assert window.context_label.text() == (
        "ASR fun-asr-realtime: succeeded | 1 references | "
        "LLM deepseek 250 ms 100->20 token | Clipboard verified"
    )


def test_overlay_renders_anchors_and_clicks_result_to_edit(qtbot) -> None:
    set_language("en")
    overlay = OverlayWindow({"completion_overlay_duration_ms": 4000})
    qtbot.addWidget(overlay)
    session = VoiceSession()
    session.set_partial("use this")
    session.add_reference("A")
    overlay.set_session_timeline(session)
    assert "[1]" in overlay.transcript.text()

    overlay.show_completion("Use A", 1, "copied")
    assert overlay.status.text() == "Completed · Copied"
    assert overlay.references.text() == "1 references · Click to edit"
    assert overlay._hide_timer.interval() == 4000
    with qtbot.waitSignal(overlay.edit_requested, timeout=1000):
        qtbot.mouseClick(overlay.frame, Qt.MouseButton.LeftButton)


def test_overlay_compose_failure_is_not_editable(qtbot) -> None:
    set_language("en")
    overlay = OverlayWindow({"completion_overlay_duration_ms": 4000})
    qtbot.addWidget(overlay)
    observed = []
    overlay.edit_requested.connect(observed.append)
    overlay.show_compose_failure("LLM timed out", 2)

    qtbot.mouseClick(overlay.frame, Qt.MouseButton.LeftButton)

    assert observed == []
    assert overlay.status.text() == "Generation failed"
    assert overlay.transcript.text() == "LLM timed out"


def test_dragging_completion_overlay_does_not_open_editor(qtbot) -> None:
    set_language("en")
    overlay = OverlayWindow({"completion_overlay_duration_ms": 4000})
    qtbot.addWidget(overlay)
    observed = []
    overlay.edit_requested.connect(lambda: observed.append(True))
    overlay.show_completion("Use A", 1, "copied")
    start = overlay.frame.rect().center()

    qtbot.mousePress(overlay.frame, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(overlay.frame, pos=start + QPoint(50, 0))
    qtbot.mouseRelease(overlay.frame, Qt.MouseButton.LeftButton, pos=start + QPoint(50, 0))

    assert observed == []


def test_settings_contains_all_nine_pages(qtbot, tmp_path) -> None:
    set_language("en")
    config = ConfigStore(tmp_path / "config.json")
    window = SettingsWindow(config, MemorySecrets())
    qtbot.addWidget(window)
    assert window.pages.count() == 9
    assert [window.nav.item(index).text() for index in range(window.nav.count())] == [
        "General", "Hotkeys", "ASR", "LLM", "Prompt", "Clipboard", "Output", "Privacy", "Advanced"
    ]
    assert isinstance(window.hotkey_edits["toggle_listening"], HotkeyCaptureEdit)
    assert all(path != "output.show_preview" for path, _, _ in window.bindings)


def test_hotkey_capture_records_pressed_combination(qtbot) -> None:
    edit = HotkeyCaptureEdit("ctrl+alt+v")
    qtbot.addWidget(edit)
    edit.show()
    qtbot.mouseClick(edit, Qt.MouseButton.LeftButton)
    qtbot.keyClick(
        edit,
        Qt.Key.Key_K,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert edit.text() == "ctrl+shift+k"


def test_hotkey_capture_escape_cancels_and_delete_clears(qtbot) -> None:
    edit = HotkeyCaptureEdit("ctrl+alt+v")
    qtbot.addWidget(edit)
    edit.show()
    qtbot.mouseClick(edit, Qt.MouseButton.LeftButton)
    qtbot.keyClick(edit, Qt.Key.Key_Escape)
    assert edit.text() == "ctrl+alt+v"

    qtbot.mouseClick(edit, Qt.MouseButton.LeftButton)
    qtbot.keyClick(edit, Qt.Key.Key_Delete)
    assert edit.text() == ""


def test_hotkey_capture_records_mouse_side_button(qtbot) -> None:
    edit = HotkeyCaptureEdit()
    qtbot.addWidget(edit)
    edit.show()
    qtbot.mouseClick(
        edit,
        Qt.MouseButton.BackButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert edit.text() == "ctrl+mouse4"


def test_hotkey_normalizes_qt_special_key_names() -> None:
    assert HotkeyManager.normalize("Ctrl+Meta+PgUp") == "ctrl+windows+page up"


def test_simplified_chinese_translation(qtbot) -> None:
    set_language("zh_CN")
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.workspace_nav.text() == "工作台"
    assert window.start_button.text() == "开始"
    set_language("en")


def test_tray_single_and_double_click_request_the_main_window(qtbot) -> None:
    tray = TrayIcon()
    spy = QSignalSpy(tray.show_requested)
    tray._activated(QSystemTrayIcon.ActivationReason.Trigger)
    tray._activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert spy.count() == 2
    tray.set_state("Armed")
    assert tray.start_action.isEnabled() is False
    tray.set_state("Composing")
    assert tray.start_action.isEnabled() is False


def test_main_window_is_moved_back_to_an_available_screen(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.move(12000, 9000)
    window.show_normal()
    assert any(
        screen.availableGeometry().intersects(window.frameGeometry())
        for screen in QApplication.screens()
    )


def test_settings_reports_entering_and_leaving_hotkeys_page(qtbot, tmp_path) -> None:
    settings = SettingsWindow(ConfigStore(tmp_path / "config.json"), MemorySecrets())
    qtbot.addWidget(settings)
    entered = QSignalSpy(settings.hotkeys_page_entered)
    exited = QSignalSpy(settings.hotkeys_page_exited)
    settings.nav.setCurrentRow(1)
    settings.nav.setCurrentRow(0)
    assert entered.count() == 1
    assert exited.count() == 1
