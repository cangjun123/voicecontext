from __future__ import annotations

import wave
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
    config.set("advanced.start_minimized", False)
    config.set("asr.streaming_partial", False)
    config.set("output.auto_paste", False)

    monkeypatch.setattr(application, "ConfigStore", lambda: config)
    monkeypatch.setattr(application, "SecretStore", MemorySecrets)
    monkeypatch.setattr(application, "SessionStore", lambda: SessionStore(tmp_path / "history.sqlite3"))
    monkeypatch.setattr(application, "set_startup", lambda enabled: None)
    monkeypatch.setattr(application.HotkeyManager, "register", lambda self, hotkeys: [])
    monkeypatch.setattr(application.VoiceContextController, "_start_audio", lambda self: setattr(self, "_audio", FakeAudio(tmp_path)))
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    assert controller._session.state.value == "Listening"
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "PromptReady", timeout=5000)
    assert "fake spoken task" in controller._session.final_prompt
    assert controller.history.recent(1)[0]["transcript"] == "fake spoken task"
    controller.copy_prompt("edited prompt")
    assert controller._session.final_prompt == "edited prompt"
    assert controller.history.get(controller._session.id)["finalPrompt"] == "edited prompt"
    assert qapp.clipboard().text() == "edited prompt"

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
    monkeypatch.setattr(application.VoiceContextController, "_start_audio", lambda self: setattr(self, "_audio", FakeAudio(tmp_path)))
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
    monkeypatch.setattr(
        application.VoiceContextController,
        "_start_audio",
        lambda self: setattr(self, "_audio", FakeAudio(tmp_path)),
    )
    monkeypatch.setattr(application.VoiceContextController, "_engine", lambda self: FakeASR())
    monkeypatch.setattr(application, "LLMComposerClient", EmptyLLMClient)
    qapp.clipboard().setText("keep me")

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
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


def test_controller_uses_true_realtime_asr_without_offline_transcription(
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
        lambda self: pytest.fail("offline ASR must not run for Aliyun realtime provider"),
    )

    controller = application.VoiceContextController(qapp, FakeSingleInstance())
    controller.start_session()
    controller._realtime_engine.transcript_updated.emit("实时识别")
    assert controller._session.transcript == "实时识别"
    controller.finish_session()
    qtbot.waitUntil(lambda: controller._session.state.value == "PromptReady", timeout=5000)
    assert controller._session.transcript == "实时识别成功。"
    assert "实时识别成功。" in controller._session.final_prompt
    assert controller._session.asr_metadata["request_id"] == "request-1"
    assert controller._session.asr_metadata["first_package_delay_ms"] == 300
    controller.shutdown()
