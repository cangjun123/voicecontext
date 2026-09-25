from __future__ import annotations

import json

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

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
    # QTextBrowser renders HTML with anchors, so plain text includes the reference marker
    assert "test speech" in window.transcript_edit.toPlainText()
    assert "[1]" in window.transcript_edit.toPlainText()
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

    window.set_composer_status({"mode": "direct_transcript"})
    assert window.context_label.text() == "Prompt ready from ASR transcript"


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

    session.composer_metadata = {"mode": "direct_transcript"}
    session.remove_reference("ref_1")
    window.set_completion_receipt(session)
    assert window.context_label.text() == (
        "ASR fun-asr-realtime: succeeded | 0 references | "
        "ASR transcript | Clipboard verified"
    )


def test_overlay_transcript_auto_scrolls_to_the_latest_words(qtbot) -> None:
    set_language("en")
    overlay = OverlayWindow({"completion_overlay_duration_ms": 4000})
    overlay.resize(460, 250)
    qtbot.addWidget(overlay)
    overlay.show_without_focus()

    session = VoiceSession()
    session.set_partial("short start")
    overlay.set_session_timeline(session)

    long_tail = " " + "and the user keeps talking well past two lines" * 30
    session.set_partial(f"short start{long_tail}")
    overlay.set_session_timeline(session)

    bar = overlay.transcript_scroll.verticalScrollBar()
    # The scroll is deferred one event-loop turn after the relayout.
    qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=5000)
    qtbot.waitUntil(lambda: bar.value() == bar.maximum(), timeout=5000)

    overlay.show_completion("Use A", 1, "copied")
    assert bar.value() == 0
    assert overlay.transcript.text() == "Use A"

    overlay.set_transcript("latest words again")
    qtbot.waitUntil(lambda: bar.value() == bar.maximum(), timeout=5000)


def test_overlay_transcript_viewport_keeps_the_dark_overlay_background(qtbot) -> None:
    """The scroll viewport must not put a light system canvas behind white text."""
    set_language("en")
    overlay = OverlayWindow({"overlay_opacity": 1.0})
    qtbot.addWidget(overlay)
    overlay.show_without_focus()

    session = VoiceSession()
    session.set_partial("Live recognized words")
    overlay.set_session_timeline(session)
    overlay.set_state("Listening")
    qtbot.waitUntil(overlay.isVisible, timeout=5000)

    viewport = overlay.transcript_scroll.viewport()
    viewport_origin = viewport.mapTo(overlay, QPoint(0, 0))
    sample = overlay.grab().toImage().pixelColor(
        viewport_origin.x() + viewport.width() // 2,
        viewport_origin.y() + viewport.height() - 8,
    )
    assert max(sample.red(), sample.green(), sample.blue()) < 100


def test_overlay_survives_bursts_of_queued_transcript_updates(qtbot) -> None:
    """Rapid streaming updates must not recurse through nested event loops."""
    set_language("en")
    overlay = OverlayWindow({"completion_overlay_duration_ms": 4000})
    qtbot.addWidget(overlay)
    overlay.set_state("Listening")

    from PySide6.QtCore import QObject, Signal

    class Engine(QObject):
        transcript_updated = Signal(str)

    engine = Engine()
    session = VoiceSession()

    def on_update(text: str) -> None:
        session.set_partial(text)
        overlay.set_session_timeline(session)

    engine.transcript_updated.connect(on_update)
    for index in range(200):  # delivered as queued events by qtbot's event loop
        engine.transcript_updated.emit(f"queued streaming update {index} " * 5)

    qtbot.waitUntil(lambda: len(overlay.transcript.text()) > 0, timeout=5000)
    bar = overlay.transcript_scroll.verticalScrollBar()
    if bar.maximum() > 0:
        assert bar.value() == bar.maximum()


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
    assert overlay.references.text() == "1 references · Double-click to edit"
    assert overlay._hide_timer.interval() == 4000
    # Single click should NOT trigger edit anymore
    qtbot.mouseClick(overlay.frame, Qt.MouseButton.LeftButton)
    # Double click should trigger edit
    with qtbot.waitSignal(overlay.edit_requested, timeout=1000):
        qtbot.mouseDClick(overlay.frame, Qt.MouseButton.LeftButton)


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
    providers = [
        window.asr_provider.itemData(index)
        for index in range(window.asr_provider.count())
    ]
    assert providers == ["aliyun_realtime", "openai_compatible", "custom_api"]
    bound_paths = {path for path, _, _ in window.bindings}
    assert "asr.model_path" not in bound_paths
    assert "asr.device" not in bound_paths


def test_settings_export_and_import_round_trip(qtbot, tmp_path, monkeypatch) -> None:
    set_language("en")
    # Hotkey availability depends on whatever else is running on this machine.
    monkeypatch.setattr(
        "voice_context.ui.settings_window.HotkeyManager.availability",
        staticmethod(lambda text: (True, "")),
    )
    secrets = MemorySecrets()
    secrets.set("asr:aliyun_realtime:api_key", "stored-asr-key")
    config = ConfigStore(tmp_path / "config.json")
    window = SettingsWindow(config, secrets)
    qtbot.addWidget(window)
    window.asr_key.setText("typed-asr-key")
    window.llm_key.setText("typed-llm-key")
    window.asr_model.setText("my-custom-model")

    export_path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "voice_context.ui.settings_window.QFileDialog.getSaveFileName",
        lambda *args, **kwargs: (str(export_path), ""),
    )
    monkeypatch.setattr(
        "voice_context.ui.settings_window.QMessageBox.information", lambda *args, **kwargs: None
    )
    window._export_settings()

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["format"] == "voicecontext-settings"
    assert payload["settings"]["asr"]["model"] == "my-custom-model"
    # The typed key belongs to the currently selected provider (openai_compatible),
    # while keys already stored for other providers ride along untouched.
    assert payload["secrets"]["asr:openai_compatible:api_key"] == "typed-asr-key"
    assert payload["secrets"]["asr:aliyun_realtime:api_key"] == "stored-asr-key"
    assert payload["secrets"]["llm:openai_compatible:api_key"] == "typed-llm-key"

    # Import into a fresh window pointed at a fresh config store.
    fresh_config = ConfigStore(tmp_path / "fresh.json")
    fresh_secrets = MemorySecrets()
    target = SettingsWindow(fresh_config, fresh_secrets)
    qtbot.addWidget(target)
    assert target.asr_model.text() != "my-custom-model"
    monkeypatch.setattr(
        "voice_context.ui.settings_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(export_path), ""),
    )
    monkeypatch.setattr(
        "voice_context.ui.settings_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    target._import_settings()
    assert target.asr_model.text() == "my-custom-model"
    assert target.asr_key.text() == "typed-asr-key"
    assert target.llm_key.text() == "typed-llm-key"

    saved = []
    target.saved.connect(lambda: saved.append(True))
    target._save()
    assert saved == [True]
    assert fresh_config.get("asr.model") == "my-custom-model"
    assert fresh_secrets.get("asr:openai_compatible:api_key") == "typed-asr-key"
    assert fresh_secrets.get("asr:aliyun_realtime:api_key") == "stored-asr-key"
    assert fresh_secrets.get("llm:openai_compatible:api_key") == "typed-llm-key"


def test_settings_import_rejects_foreign_files(qtbot, tmp_path, monkeypatch) -> None:
    set_language("en")
    config = ConfigStore(tmp_path / "config.json")
    window = SettingsWindow(config, MemorySecrets())
    qtbot.addWidget(window)
    bad = tmp_path / "foreign.json"
    bad.write_text('{"hello": "world"}', encoding="utf-8")
    monkeypatch.setattr(
        "voice_context.ui.settings_window.QFileDialog.getOpenFileName",
        lambda *args, **kwargs: (str(bad), ""),
    )
    failures = []
    monkeypatch.setattr(
        "voice_context.ui.settings_window.QMessageBox.critical",
        lambda parent, title, text: failures.append(text),
    )
    window._import_settings()
    assert len(failures) == 1
    assert "not a VoiceContext settings export" in failures[0]


def test_settings_switches_between_remote_asr_defaults(qtbot, tmp_path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    window = SettingsWindow(config, MemorySecrets())
    qtbot.addWidget(window)

    window.asr_provider.setCurrentIndex(window.asr_provider.findData("aliyun_realtime"))
    assert window.asr_model.text() == "fun-asr-realtime"
    assert window.asr_base_url.text() == "https://dashscope.aliyuncs.com/api/v1"

    window.asr_provider.setCurrentIndex(window.asr_provider.findData("openai_compatible"))
    assert window.asr_model.text() == "whisper-1"
    assert window.asr_base_url.text() == "https://api.openai.com/v1"


def test_settings_distinguishes_identical_microphone_names(qtbot, tmp_path, monkeypatch) -> None:
    devices = [
        {"index": 1, "name": "Microphone", "hostapi_name": "MME", "id": "pa:mme"},
        {"index": 2, "name": "Microphone", "hostapi_name": "Windows WASAPI", "id": "pa:wasapi"},
    ]
    monkeypatch.setattr("voice_context.ui.settings_window.AudioCapture.devices", lambda: devices)
    config = ConfigStore(tmp_path / "config.json")
    config.set("asr.input_device", "pa:wasapi")
    window = SettingsWindow(config, MemorySecrets())
    qtbot.addWidget(window)

    assert window.asr_input_device.count() == 3
    assert window.asr_input_device.itemText(1) == "Microphone (MME)"
    assert window.asr_input_device.itemText(2) == "Microphone (Windows WASAPI)"
    assert window.asr_input_device.currentData() == "pa:wasapi"


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
