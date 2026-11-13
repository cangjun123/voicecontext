from __future__ import annotations

import wave
import time
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal

import voice_context.app as application
from voice_context.asr import RealtimeRecognitionResult
from voice_context.asr.base import TranscriptionResult
from voice_context.output.dispatcher import ClipboardWriteResult
from voice_context.storage.config_store import ConfigStore
from voice_context.storage.session_store import SessionStore


class MemorySecrets:
    def get(self, name: str) -> str:
        return "test-key"

    def set(self, name: str, value: str) -> None:
        pass

    @staticmethod
    def key_for(section: str, provider: str) -> str:
        return f"{section}:{provider}:api_key"


class FakeAudio(QObject):
    level_changed = Signal(float)
    silence_detected = Signal()
    error = Signal(str)

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self.duration_ms = 650

    def start(self) -> None:
        pass

    def stop(self):
        path = self.directory / "fake.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * 16000)
        return path

    def discard(self) -> None:
        pass


class FakeRealtimeAudio(FakeAudio):
    def __init__(self, config) -> None:
        super().__init__(Path(config["test_directory"]))
        self.consumer = None

    def set_chunk_consumer(self, consumer) -> None:
        self.consumer = consumer

    def stop_stream(self) -> None:
        pass


class SlowStartAudio(FakeAudio):
    def start(self) -> None:
        time.sleep(0.25)


class FakeRealtimeASR(QObject):
    transcript_updated = Signal(str)
    error = Signal(str)

    def __init__(self, config, api_key) -> None:
        super().__init__()
        self.running = False

    def start(self) -> None:
        self.running = True

    def send_audio(self, data: bytes) -> None:
        pass

    def stop(self):
        self.running = False
        return RealtimeRecognitionResult(
            text="实时识别成功。",
            segments=[{"text": "实时识别成功。", "start_ms": 0, "end_ms": 900}],
            request_id="request-1",
            first_package_delay_ms=300,
            last_package_delay_ms=100,
        )


class FakeASR:
    def transcribe(self, path):
        return TranscriptionResult("fake spoken task")


class FakeLLMClient:
    def __init__(self, config, api_key) -> None:
        self.last_response_text = ""
        self.last_metadata = {}

    def rewrite(self, transcript, timeline, references) -> str:
        self.last_response_text = "LLM rewrote the spoken task"
        self.last_metadata = {
            "request_id": "llm-request-1",
            "response_model": "actual-test-model",
            "usage": {"completion_tokens": 6},
            "streamed": True,
        }
        return self.last_response_text


class EmptyLLMClient:
    def __init__(self, config, api_key) -> None:
        self.last_response_text = ""
        self.last_metadata = {"streamed": True, "usage": {"completion_tokens": 2048}}

    def rewrite(self, transcript, timeline, references) -> str:
        return ""


class FakeSingleInstance(QObject):
    message_received = Signal(str)


def test_controller_composes_and_saves_a_session(qtbot, qapp, monkeypatch, tmp_path) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("general.language", "en")
    config.set("advanced.start_minimized", False)
    config.set("asr.streaming_partial", False)
    config.set("output.auto_paste", False)

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda config: FakeAudio(tmp_path))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Listening", timeout=5000)
    assert controller._session.state.value == "Listening"
    controller.finish_session()
    assert controller._session.state.value == "Composing"
    controller.toggle_session()
    assert controller._session.state.value == "Composing"
    assert "being generated" in controller.overlay.transcript.text()
    qtbot.waitUntil(lambda: controller._session.state.value == "PromptReady", timeout=5000)
    assert "fake spoken task" in controller._session.final_prompt
    assert controller.history.recent(1)[0]["transcript"] == "fake spoken task"
    pending_copies = []
    controller.output.copy_verified = lambda text, callback: pending_copies.append((text, callback))
    controller.copy_prompt("edited prompt")
    assert controller._session.final_prompt == "edited prompt"
    assert controller.history.get(controller._session.id)["finalPrompt"] == "edited prompt"
    assert pending_copies[0][0] == "edited prompt"
    pending_copies[0][1](ClipboardWriteResult(True, 1))
    assert controller.history.get(controller._session.id)["output"]["clipboard_verified"] is True

    pasted = []
    config.set("output.auto_paste", True)
    controller._automatic_paste = lambda: pasted.append(True)
    controller._prompt_delivery_done(
        controller._session.id,
        ClipboardWriteResult(False, 3, "busy"),
        copy_requested=True,
    )
    assert pasted == []
    controller.shutdown()


def test_controller_reloads_config_and_records_llm_result(qtbot, qapp, monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = ConfigStore(config_path)
    config.set("advanced.start_minimized", False)
    config.set("general.language", "en")
    config.set("asr.streaming_partial", False)
    config.set("output.auto_paste", False)
    config.set("llm.enabled", False)
    config.save()

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda config: FakeAudio(tmp_path))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())
    monkeypatch.setattr(application, "LLMComposerClient", FakeLLMClient)

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    shown = []
    controller.show_main = lambda: shown.append(True)
    settings_store = ConfigStore(config_path)
    settings_store.set("llm.enabled", True)
    settings_store.set("llm.model", "configured-test-model")
    settings_store.save()

    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Listening", timeout=5000)
    controller._session.add_reference("captured context")
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "PromptReady", timeout=5000)

    assert "LLM rewrote the spoken task" in controller._session.final_prompt
    assert controller._session.composer_metadata["requested"] is True
    assert controller._session.composer_metadata["used"] is True
    assert controller._session.composer_metadata["request_id"] == "llm-request-1"
    assert "LLM actual-test-model" in controller.main.context_label.text()
    assert "Clipboard verified" in controller.main.context_label.text()
    assert controller.history.get(controller._session.id)["composer"]["used"] is True
    assert controller.history.get(controller._session.id)["output"]["clipboard_verified"] is True
    assert shown == []
    controller._fail_session("unexpected post-ASR failure")
    assert controller._session.asr_metadata["status"] == "success"
    controller.shutdown()


def test_controller_stops_on_empty_llm_response_without_overwriting_clipboard(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("general.language", "en")
    config.set("asr.streaming_partial", False)
    config.set("output.copy_to_clipboard", True)
    config.set("llm.enabled", True)
    config.save()

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda config: FakeAudio(tmp_path))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())
    monkeypatch.setattr(application, "LLMComposerClient", EmptyLLMClient)
    qapp.clipboard().setText("keep me")

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Listening", timeout=5000)
    controller._session.add_reference("captured context")
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Error", timeout=5000)

    assert controller._session.final_prompt == ""
    assert controller._session.composer_metadata["failure_reason"] == "empty_response"
    assert controller._session.output_metadata["status"] == "skipped"
    assert qapp.clipboard().text() == "keep me"
    assert controller.overlay.status.text() == "Generation failed"
    stored = controller.history.get(controller._session.id)
    assert stored and stored["error"] == "LLM returned an empty task."
    controller.shutdown()


def test_controller_uses_plain_asr_transcript_without_references_or_llm(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("general.language", "en")
    config.set("asr.streaming_partial", False)
    config.set("llm.enabled", True)
    config.set("prompt.target", "generic_markdown")
    config.save()

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda values: FakeAudio(tmp_path))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())
    monkeypatch.setattr(
        application,
        "LLMComposerClient",
        lambda *_args, **_kwargs: pytest.fail("LLM must not be created without references"),
    )

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state == application.SessionState.LISTENING)
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state == application.SessionState.PROMPT_READY)

    assert controller._session.references == []
    assert controller._session.final_prompt == "fake spoken task"
    assert controller._session.composer_metadata == {
        "requested": False,
        "used": False,
        "provider": "openai_compatible",
        "model": "qwen2.5-coder",
        "mode": "direct_transcript",
        "skip_reason": "no_references",
        "llm_configured": True,
    }
    assert "| ASR transcript |" in controller.main.context_label.text()
    controller.shutdown()


def test_controller_fails_clearly_when_nothing_was_recognized(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("general.language", "en")
    config.set("asr.provider", "aliyun_realtime")
    config.set("asr.model", "fun-asr-realtime")
    config.set("llm.enabled", True)
    config.set("output.auto_paste", False)

    class SilentRealtimeASR(FakeRealtimeASR):
        def stop(self):
            self.running = False
            return RealtimeRecognitionResult(text="", segments=[])

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AliyunRealtimeASR", SilentRealtimeASR)
    monkeypatch.setattr(application, "AudioCapture", lambda values: FakeRealtimeAudio({"test_directory": str(tmp_path)}))
    monkeypatch.setattr(
        application,
        "LLMComposerClient",
        lambda *_args, **_kwargs: pytest.fail("LLM must not be created for an empty session"),
    )

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Listening", timeout=5000)
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Error", timeout=5000)

    assert controller._session.final_prompt == ""
    assert controller._session.error == "No speech was recognized."
    assert controller._session.composer_metadata["mode"] == "empty_session"
    assert controller._session.composer_metadata["skip_reason"] == "no_speech_and_no_references"
    assert controller._session.composer_metadata["requested"] is False
    assert controller._session.output_metadata["status"] == "skipped"
    assert controller.overlay.status.text() == "Generation failed"
    stored = controller.history.get(controller._session.id)
    assert stored and stored["error"] == "No speech was recognized."
    controller.shutdown()


def test_controller_uses_true_realtime_asr_without_file_upload_transcription(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("advanced.test_directory", str(tmp_path))
    config.set("asr.provider", "aliyun_realtime")
    config.set("asr.model", "fun-asr-realtime")
    config.set("output.auto_paste", False)

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", FakeRealtimeAudio)
    monkeypatch.setattr(application, "AliyunRealtimeASR", FakeRealtimeASR)
    monkeypatch.setattr(
        application.VoiceContextController,
        "_engine",
        lambda self: pytest.fail("file-upload ASR must not run for Aliyun realtime provider"),
    )

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Listening", timeout=5000)
    controller._realtime_engine.transcript_updated.emit("实时识别")
    assert controller._session.transcript == "实时识别"
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "PromptReady", timeout=5000)
    assert controller._session.transcript == "实时识别成功。"
    assert "实时识别成功。" in controller._session.final_prompt
    assert controller._session.asr_metadata["request_id"] == "request-1"
    assert controller._session.asr_metadata["first_package_delay_ms"] == 300
    controller.shutdown()


def test_slow_audio_start_does_not_block_the_gui_thread(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("asr.streaming_partial", False)
    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda values: SlowStartAudio(tmp_path))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    started = time.perf_counter()
    controller.start_session()
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1
    assert controller._session.state == application.SessionState.ARMED
    controller.finish_session()
    assert controller._finish_when_ready is True
    qtbot.waitUntil(lambda: controller._session.state == application.SessionState.PROMPT_READY)
    controller.shutdown()


def test_silence_split_restarts_capture_before_transcription_finishes(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("asr.streaming_partial", False)
    started = threading.Event()
    release = threading.Event()
    audio_instances = []

    class BlockingASR:
        def transcribe(self, path):
            started.set()
            release.wait(5)
            return TranscriptionResult("first segment")

    def create_audio(_config):
        audio = FakeAudio(tmp_path)
        audio_instances.append(audio)
        return audio

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", create_audio)
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: BlockingASR())

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state == application.SessionState.LISTENING)
    first_audio = audio_instances[0]
    try:
        first_audio.silence_detected.emit()
        qtbot.waitUntil(started.is_set, timeout=5000)
        qtbot.waitUntil(
            lambda: len(audio_instances) >= 2 and controller._audio is audio_instances[1],
            timeout=5000,
        )
        assert controller._session.state == application.SessionState.LISTENING
    finally:
        release.set()
    qtbot.waitUntil(lambda: controller._pending_segments == 0, timeout=5000)
    assert controller._session.transcript == "first segment"
    controller.cancel_session()
    controller.shutdown()


def test_realtime_capture_starts_mic_before_websocket_and_flushes_prebuffer(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("asr.provider", "aliyun_realtime")
    config.set("asr.model", "fun-asr-realtime")
    config.set("output.copy_to_clipboard", False)
    events: list[str] = []
    received_chunks: list[bytes] = []

    class PrebufferAudio(FakeAudio):
        def __init__(self, _cfg) -> None:
            super().__init__(tmp_path)
            self._prebuffered: list[bytes] = []
            self.consumer = None

        def start(self) -> None:
            events.append("audio.start")
            # Speech uttered while WebSocket is still connecting
            self._prebuffered.append(b"\x01\x02" * 400)

        def flush_and_set_chunk_consumer(self, consumer) -> None:
            events.append("audio.flush")
            for chunk in self._prebuffered:
                consumer(chunk)
            self.consumer = consumer

    class HandshakeASR(FakeRealtimeASR):
        def start(self) -> None:
            events.append("engine.start")
            super().start()

        def send_audio(self, data: bytes) -> None:
            received_chunks.append(data)

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", PrebufferAudio)
    monkeypatch.setattr(application, "AliyunRealtimeASR", HandshakeASR)

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "Listening", timeout=5000)
    assert events == ["audio.start", "engine.start", "audio.flush"]
    assert received_chunks == [b"\x01\x02" * 400]
    controller.cancel_session()
    controller.shutdown()


def test_controller_polishes_without_references_when_skip_disabled_and_streams_overlay(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    config.set("general.language", "en")
    config.set("asr.streaming_partial", False)
    config.set("output.copy_to_clipboard", False)
    config.set("llm.enabled", True)
    config.set("llm.skip_without_references", False)
    config.save()

    stream_released = threading.Event()
    first_chunk_sent = threading.Event()

    class StreamingPolishLLM:
        def __init__(self, cfg, api_key) -> None:
            self.on_stream = None
            self.last_response_text = ""
            self.last_metadata = {"streamed": True, "response_model": "polisher-v1"}

        def rewrite(self, transcript, timeline, references) -> str:
            assert references == []
            if self.on_stream:
                self.on_stream("Polished live chunk")
                first_chunk_sent.set()
            stream_released.wait(5)
            self.last_response_text = "Polished final instruction"
            return self.last_response_text

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda values: FakeAudio(tmp_path))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())
    monkeypatch.setattr(application, "LLMComposerClient", StreamingPolishLLM)

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    qtbot.waitUntil(lambda: controller._session.state == application.SessionState.LISTENING)
    controller.finish_session()
    qtbot.waitUntil(first_chunk_sent.is_set, timeout=5000)
    qtbot.waitUntil(
        lambda: controller.overlay.transcript.text() == "Polished live chunk",
        timeout=5000,
    )
    stream_released.set()
    qtbot.waitUntil(lambda: controller._session.state == application.SessionState.PROMPT_READY)
    assert controller._session.final_prompt == "Polished final instruction"

    # Inline edit save from overlay updates prompt, copies quietly, and restores focus
    activated = []
    monkeypatch.setattr(
        application,
        "get_foreground_info",
        lambda: application.ForegroundInfo(
            process="Code.exe", title="Editor", pid=999999, hwnd=98765
        ),
    )
    monkeypatch.setattr(application, "activate_window", lambda hwnd: activated.append(hwnd) or True)
    controller.output.copy_verified = lambda text, cb: cb(ClipboardWriteResult(True, 1))
    controller.overlay.start_inline_edit()
    assert controller._inline_return_hwnd == 98765
    controller.overlay.editor.setPlainText("Inline edited instruction")
    controller.overlay._commit_inline_edit()
    assert activated == [98765]
    assert controller._session.final_prompt == "Inline edited instruction"
    assert controller.overlay.isVisible() is False
    controller.shutdown()


def test_controller_microphone_asr_test_records_and_transcribes(
    qtbot, qapp, monkeypatch, tmp_path
) -> None:
    config = ConfigStore(tmp_path / "config.json")
    config.set("advanced.start_minimized", False)
    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application, "AudioCapture", lambda _cfg: FakeAudio(tmp_path))
    monkeypatch.setattr(application, "create_asr_engine", lambda cfg, key: FakeASR())

    dialogs: list[tuple[str, str]] = []
    monkeypatch.setattr(
        application.QMessageBox,
        "information",
        lambda parent, title, text: dialogs.append((title, text)),
    )

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller._test_asr_mic({"provider": "openai_compatible"}, "key", duration_s=0.01)
    qtbot.waitUntil(lambda: len(dialogs) == 1, timeout=5000)
    assert dialogs == [("ASR test succeeded", "fake spoken task")]
    assert not (tmp_path / "fake.wav").exists()
    controller.shutdown()
