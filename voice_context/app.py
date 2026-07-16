from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from voice_context.asr import AliyunRealtimeASR, RealtimeRecognitionResult, create_asr_engine
from voice_context.core.composer import ComposeOptions, RuleComposer
from voice_context.core.events import ReferenceEvent
from voice_context.core.session import SessionState, VoiceSession
from voice_context.input.audio_capture import AudioCapture
from voice_context.input.clipboard_watcher import ClipboardWatcher
from voice_context.input.foreground import ForegroundInfo, activate_window, get_foreground_info
from voice_context.input.hotkeys import HotkeyManager
from voice_context.llm import LLMComposerClient, LLMError
from voice_context.output.dispatcher import OutputDispatcher
from voice_context.platform.startup import set_startup
from voice_context.storage.config_store import APP_DIR, ConfigStore
from voice_context.storage.secrets import SecretStore
from voice_context.storage.session_store import SessionStore
from voice_context.ui.main_window import MainWindow
from voice_context.ui.i18n import set_language
from voice_context.ui.overlay import OverlayWindow
from voice_context.ui.settings_window import SettingsWindow
from voice_context.ui.theme import apply_theme
from voice_context.ui.tray import TrayIcon, create_tray_icon


LOG = logging.getLogger("voice_context")


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class SingleInstance(QObject):
    message_received = Signal(str)
    NAME = "VoiceContext-6A45E04B-5B19-4B4B-A3D8"

    @classmethod
    def acquire(cls) -> SingleInstance | None:
        probe = QLocalSocket()
        probe.connectToServer(cls.NAME)
        if probe.waitForConnected(150):
            probe.write(b"show")
            probe.waitForBytesWritten(150)
            probe.disconnectFromServer()
            return None
        QLocalServer.removeServer(cls.NAME)
        instance = cls()
        instance.server = QLocalServer(instance)
        if not instance.server.listen(cls.NAME):
            return None
        instance.server.newConnection.connect(instance._accept)
        return instance

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            socket.waitForReadyRead(100)
            message = bytes(socket.readAll()).decode("utf-8", "replace") or "show"
            self.message_received.emit(message)
            socket.disconnectFromServer()


class VoiceContextController(QObject):
    def __init__(self, app: QApplication, single_instance: SingleInstance) -> None:
        super().__init__()
        self.app = app
        self.single_instance = single_instance
        self.config = ConfigStore()
        set_language(str(self.config.get("general.language", "en")))
        self.secrets = SecretStore()
        self.history = SessionStore()
        self.composer = RuleComposer()
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="voicecontext")
        self._tasks: set[TaskSignals] = set()
        self._asr_lock = threading.Lock()
        self._asr_engine = None
        self._audio: AudioCapture | None = None
        self._session: VoiceSession | None = None
        self._target_window = ForegroundInfo()
        self._original_clipboard: tuple[str, str] | None = None
        self._original_clipboard_session_id: str | None = None
        self._segment_start_ms = 0
        self._pending_segments = 0
        self._compose_when_ready = False
        self._asr_warnings: list[str] = []
        self._partial_inflight = False
        self._generation = 0
        self._audio_generation = 0
        self._realtime_engine: AliyunRealtimeASR | None = None
        self._realtime_closing = False
        self._hold_owns_session = False
        self._settings_window: SettingsWindow | None = None

        apply_theme(app, str(self.config.get("general.theme", "system")))
        app.setWindowIcon(create_tray_icon())
        app.setQuitOnLastWindowClosed(False)
        self.main = MainWindow()
        self.overlay = OverlayWindow(self.config.section("general"))
        self.tray = TrayIcon(self)
        self.hotkeys = HotkeyManager()
        self.clipboard_watcher = ClipboardWatcher(app.clipboard(), self.config.section("clipboard"))
        self.output = OutputDispatcher(app.clipboard())
        self.partial_timer = QTimer(self)
        self.partial_timer.timeout.connect(self._request_partial)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(250)
        self.elapsed_timer.timeout.connect(self._update_elapsed)
        self._connect_signals()
        self.main.set_state(SessionState.IDLE)
        self._apply_runtime_settings(initial=True)
        self._refresh_history()
        self.tray.show()
        self.single_instance.message_received.connect(lambda _: self.show_main())
        self.app.aboutToQuit.connect(self.shutdown)

        if not self.config.get("advanced.start_minimized", True) or not QSystemTrayIcon.isSystemTrayAvailable():
            self.show_main()
        else:
            default_mode = str(self.config.get("general.default_mode", "hold"))
            shortcut = self.config.get(
                "hotkeys.toggle_listening" if default_mode == "toggle" else "hotkeys.hold_to_talk"
            )
            self.tray.showMessage(
                "VoiceContext",
                f"Running in the tray. Use {shortcut} to speak.",
                QSystemTrayIcon.MessageIcon.Information,
                2200,
            )

    def _connect_signals(self) -> None:
        self.main.start_requested.connect(self.start_session)
        self.main.finish_requested.connect(self.finish_session)
        self.main.pause_requested.connect(self.pause_resume)
        self.main.cancel_requested.connect(self.cancel_session)
        self.main.settings_requested.connect(self.show_settings)
        self.main.copy_requested.connect(self.copy_prompt)
        self.main.paste_requested.connect(self.paste_prompt)
        self.main.regenerate_requested.connect(self.regenerate)
        self.main.export_requested.connect(self.export_prompt)
        self.main.reference_delete_requested.connect(self.delete_reference)
        self.main.reference_update_requested.connect(self.update_reference)
        self.main.history_selected.connect(self._load_history_prompt)
        self.main.history_delete_requested.connect(self._delete_history)

        self.tray.start_requested.connect(self.start_session)
        self.tray.finish_requested.connect(self.finish_session)
        self.tray.pause_requested.connect(self.pause_resume)
        self.tray.show_requested.connect(self.show_main)
        self.tray.settings_requested.connect(self.show_settings)
        self.tray.recent_requested.connect(self.show_history)
        self.tray.exit_requested.connect(self.app.quit)

        self.hotkeys.hold_started.connect(self._hold_start)
        self.hotkeys.hold_released.connect(self._hold_release)
        self.hotkeys.toggle_requested.connect(self.toggle_session)
        self.hotkeys.finish_requested.connect(self.finish_session)
        self.hotkeys.cancel_requested.connect(self.cancel_session)
        self.hotkeys.pause_requested.connect(self.pause_resume)
        self.hotkeys.undo_reference_requested.connect(self.undo_reference)
        self.hotkeys.ignore_reference_requested.connect(self.ignore_reference)
        self.hotkeys.overlay_requested.connect(self.toggle_overlay)
        self.hotkeys.recopy_requested.connect(self.recopy_last_prompt)
        self.hotkeys.registration_error.connect(self._show_hotkey_error)
        self.clipboard_watcher.captured.connect(self._clipboard_captured)

    def _apply_runtime_settings(self, initial: bool = False) -> None:
        apply_theme(self.app, str(self.config.get("general.theme", "system")))
        self.overlay.config = self.config.section("general")
        self.overlay.setWindowOpacity(float(self.config.get("general.overlay_opacity", 0.94)))
        self.clipboard_watcher.config = self.config.section("clipboard")
        errors = self.hotkeys.register(self.config.section("hotkeys"))
        try:
            set_startup(bool(self.config.get("general.startup", False)))
        except OSError as exc:
            LOG.warning("Could not update startup registration: %s", exc)
        if self.config.get("privacy.auto_cleanup", True):
            self.history.cleanup(int(self.config.get("general.history_days", 30)))
        if errors and not initial:
            QMessageBox.warning(self.main, "Global hotkey conflict", "\n".join(errors))

    @property
    def active(self) -> bool:
        return bool(
            self._session
            and self._session.state in {SessionState.LISTENING, SessionState.PAUSED, SessionState.COMPOSING}
        )

    def _uses_realtime_asr(self) -> bool:
        return self.config.get("asr.provider") == "aliyun_realtime"

    def start_session(self) -> None:
        if self.active:
            return
        if self._session and self._session.state == SessionState.ERROR:
            self._session.transition(SessionState.IDLE)
        self._generation += 1
        self._target_window = get_foreground_info()
        mime = self.app.clipboard().mimeData()
        self._original_clipboard = (
            self.app.clipboard().text(),
            mime.html() if mime is not None and mime.hasHtml() else "",
        )
        self._session = VoiceSession(source_app=self._target_window.process)
        self._original_clipboard_session_id = self._session.id
        self._pending_segments = 0
        self._compose_when_ready = False
        self._asr_warnings.clear()
        self._partial_inflight = False
        self._asr_engine = None
        self._session.transition(SessionState.LISTENING)
        self._segment_start_ms = 0
        try:
            if self._uses_realtime_asr():
                self._start_realtime_engine()
            self._start_audio()
        except Exception as exc:
            self._fail_session(f"Could not start listening: {exc}")
            return
        self.clipboard_watcher.start()
        interval = max(800, int(float(self.config.get("asr.partial_interval_s", 2.5)) * 1000))
        if not self._uses_realtime_asr() and self.config.get("asr.streaming_partial", True):
            self.partial_timer.start(interval)
        self.elapsed_timer.start()
        self.main.set_session(self._session)
        self._update_state_ui()
        self.overlay.set_references([])
        self.overlay.set_transcript("")
        self.overlay.set_state("Listening")

    def _start_audio(self) -> None:
        self._audio_generation += 1
        audio_config = self.config.section("asr")
        audio_config.update(self.config.section("advanced"))
        self._audio = AudioCapture(audio_config)
        self._audio.level_changed.connect(self.overlay.set_level)
        self._audio.error.connect(self._audio_error)
        if self._uses_realtime_asr():
            if self._realtime_engine is None:
                raise RuntimeError("Aliyun realtime ASR is not connected.")
            self._audio.set_chunk_consumer(self._realtime_engine.send_audio)
        else:
            self._audio.silence_detected.connect(self._silence_split)
        self._audio.start()
        if self._session:
            self._segment_start_ms = self._session.elapsed_ms()

    def _audio_error(self, message: str) -> None:
        if message.startswith("Realtime audio delivery failed"):
            self._fail_session(message)
        else:
            LOG.warning("Audio stream: %s", message)

    def _start_realtime_engine(self) -> None:
        if not self._session:
            raise RuntimeError("No active session for realtime ASR.")
        config = self.config.section("asr")
        key = self.secrets.get(SecretStore.key_for("asr", "aliyun_realtime"))
        engine = AliyunRealtimeASR(config, key)
        session_id = self._session.id
        engine.transcript_updated.connect(
            lambda text, current=engine, sid=session_id: self._realtime_transcript(
                current, sid, text
            )
        )
        engine.error.connect(
            lambda message, current=engine, sid=session_id: self._realtime_error(
                current, sid, message
            )
        )
        engine.start()
        self._realtime_engine = engine
        self._realtime_closing = False

    def _realtime_transcript(
        self, engine: AliyunRealtimeASR, session_id: str, text: str
    ) -> None:
        if (
            not self._session
            or self._session.id != session_id
            or self._realtime_engine is not engine
            or self._session.state != SessionState.LISTENING
        ):
            return
        self._session.set_partial(text)
        self.main.update_transcript(self._session.transcript)
        self.overlay.set_transcript(self._session.transcript)

    def _realtime_error(
        self, engine: AliyunRealtimeASR, session_id: str, message: str
    ) -> None:
        if (
            self._session
            and self._session.id == session_id
            and self._realtime_engine is engine
            and self._session.state == SessionState.LISTENING
        ):
            self._fail_session(f"Aliyun realtime ASR failed: {message}")

    def _silence_split(self) -> None:
        """Finish a spoken segment after sustained silence and immediately arm the next one."""
        if not self._session or self._session.state != SessionState.LISTENING or not self._audio:
            return
        if self._audio.duration_ms < 500:
            return
        # The snapshot belongs to the segment that is about to be closed; allow the next segment to stream independently.
        self._partial_inflight = False
        self._stop_and_submit_segment()
        try:
            self._start_audio()
        except Exception as exc:
            self._fail_session(f"Could not continue after silence: {exc}")

    def toggle_session(self) -> None:
        if self._session and self._session.state in {SessionState.LISTENING, SessionState.PAUSED}:
            self.finish_session()
        elif not self.active:
            self.start_session()

    def _hold_start(self) -> None:
        self._hold_owns_session = not self.active
        if self._hold_owns_session:
            self.start_session()

    def _hold_release(self) -> None:
        if self._hold_owns_session:
            self._hold_owns_session = False
            self.finish_session()

    def pause_resume(self) -> None:
        if not self._session:
            return
        if self._session.state == SessionState.LISTENING:
            self.partial_timer.stop()
            self._session.transition(SessionState.PAUSED)
            if self._uses_realtime_asr():
                self._stop_realtime_capture()
            else:
                self._stop_and_submit_segment()
            self.overlay.set_state("Paused", self._session.transcript or "Recording paused")
        elif self._session.state == SessionState.PAUSED:
            if self._uses_realtime_asr() and self._realtime_closing:
                self.overlay.set_state("Paused", "Finalizing the previous speech segment...")
                return
            try:
                if self._uses_realtime_asr():
                    self._start_realtime_engine()
                self._start_audio()
            except Exception as exc:
                self._fail_session(f"Could not resume the microphone: {exc}")
                return
            self._session.transition(SessionState.LISTENING)
            if not self._uses_realtime_asr() and self.config.get("asr.streaming_partial", True):
                self.partial_timer.start(
                    max(800, int(float(self.config.get("asr.partial_interval_s", 2.5)) * 1000))
                )
            self.overlay.set_state("Listening", self._session.transcript)
        self._update_state_ui()

    def finish_session(self) -> None:
        if not self._session or self._session.state not in {SessionState.LISTENING, SessionState.PAUSED}:
            return
        self._generation += 1
        self.partial_timer.stop()
        self.elapsed_timer.stop()
        self.clipboard_watcher.stop()
        was_listening = self._session.state == SessionState.LISTENING
        self._session.transition(SessionState.COMPOSING)
        self._compose_when_ready = True
        if was_listening:
            if self._uses_realtime_asr():
                self._stop_realtime_capture()
            else:
                self._stop_and_submit_segment()
        self._update_state_ui()
        self.overlay.set_state("Composing", "Transcribing and preparing your prompt...")
        if self._pending_segments == 0:
            self._begin_compose()

    def cancel_session(self) -> None:
        if not self._session or not self.active:
            return
        self._generation += 1
        self.partial_timer.stop()
        self.elapsed_timer.stop()
        self.clipboard_watcher.stop()
        if self._audio:
            if self._uses_realtime_asr():
                self._audio.stop_stream()
            self._audio.discard()
            self._audio = None
        engine, self._realtime_engine = self._realtime_engine, None
        if engine is not None:
            self._submit(
                engine.stop,
                lambda _: None,
                lambda error: LOG.warning("Could not cancel realtime ASR: %s", error),
            )
        self._realtime_closing = False
        self._session = VoiceSession()
        self.main.set_session(self._session)
        self._update_state_ui()
        self.overlay.set_state("Idle", "Session cancelled")

    def _stop_realtime_capture(self) -> None:
        if self._audio:
            audio, self._audio = self._audio, None
            audio.stop_stream()
            audio.discard()
        engine, self._realtime_engine = self._realtime_engine, None
        if engine is None:
            return
        self._pending_segments += 1
        self._realtime_closing = True
        session_id = self._session.id if self._session else ""
        start_ms = self._segment_start_ms
        self._submit(
            engine.stop,
            lambda result: self._realtime_stopped(result, session_id, start_ms),
            lambda error: self._realtime_stop_failed(error, session_id),
        )

    def _realtime_stopped(
        self, result: RealtimeRecognitionResult, session_id: str, start_ms: int
    ) -> None:
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        self._realtime_closing = False
        self._session.set_partial("")
        segments = result.segments
        if not segments and result.text.strip():
            segments = [
                {
                    "text": result.text,
                    "start_ms": 0,
                    "end_ms": max(0, self._session.elapsed_ms() - start_ms),
                }
            ]
        for segment in segments:
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            relative_start = int(segment.get("start_ms", 0) or 0)
            relative_end = int(segment.get("end_ms", relative_start) or relative_start)
            self._session.add_speech(
                text,
                start_ms + relative_start,
                start_ms + max(relative_start, relative_end),
                is_final=True,
            )
        self.main.update_transcript(self._session.transcript)
        self.overlay.set_transcript(self._session.transcript)
        LOG.info(
            "Aliyun realtime ASR request %s: first package %.0f ms, final package %.0f ms",
            result.request_id,
            result.first_package_delay_ms,
            result.last_package_delay_ms,
        )
        if self._compose_when_ready and self._pending_segments == 0:
            self._begin_compose()

    def _realtime_stop_failed(self, error: str, session_id: str) -> None:
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        self._realtime_closing = False
        self._fail_session(f"Aliyun realtime ASR failed: {error}")

    def _stop_and_submit_segment(self) -> None:
        if not self._audio:
            return
        audio = self._audio
        self._audio = None
        end_ms = self._session.elapsed_ms() if self._session else self._segment_start_ms
        try:
            path = audio.stop()
        except Exception as exc:
            self._asr_warnings.append(f"Could not finalize audio: {exc}")
            return
        if audio.duration_ms < 120:
            self._cleanup_audio(path)
            return
        start_ms = self._segment_start_ms
        self._pending_segments += 1
        session_id = self._session.id if self._session else ""

        def transcribe():
            with self._asr_lock:
                return self._engine().transcribe(path)

        self._submit(
            transcribe,
            lambda result: self._segment_done(result, start_ms, end_ms, path, session_id),
            lambda error: self._segment_failed(error, path, session_id),
        )

    def _segment_done(self, result, start_ms: int, end_ms: int, path: Path, session_id: str) -> None:
        self._cleanup_audio(path)
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        if result.text.strip():
            self._session.add_speech(result.text, start_ms, end_ms, is_final=True)
            self.main.update_transcript(self._session.transcript)
            self.overlay.set_transcript(self._session.transcript)
        if self._compose_when_ready and self._pending_segments == 0:
            self._begin_compose()

    def _segment_failed(self, error: str, path: Path, session_id: str) -> None:
        self._cleanup_audio(path)
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        self._asr_warnings.append(error)
        if self._compose_when_ready and self._pending_segments == 0:
            self._begin_compose()
        elif self._session:
            self.overlay.set_state(self._session.state.value, f"ASR warning: {error}")

    def _request_partial(self) -> None:
        if (
            self._partial_inflight
            or not self._audio
            or not self._session
            or self._session.state != SessionState.LISTENING
            or self._audio.duration_ms < 500
        ):
            return
        try:
            path = self._audio.snapshot()
        except Exception as exc:
            LOG.warning("Could not create partial audio snapshot: %s", exc)
            return
        self._partial_inflight = True
        generation = self._generation
        audio_generation = self._audio_generation

        def transcribe():
            with self._asr_lock:
                return self._engine().transcribe(path)

        self._submit(
            transcribe,
            lambda result: self._partial_done(result, path, generation, audio_generation),
            lambda error: self._partial_failed(error, path, generation, audio_generation),
        )

    def _partial_done(self, result, path: Path, generation: int, audio_generation: int) -> None:
        self._cleanup_audio(path)
        if (
            generation != self._generation
            or audio_generation != self._audio_generation
            or not self._session
            or self._session.state != SessionState.LISTENING
        ):
            return
        self._partial_inflight = False
        self._session.set_partial(result.text)
        self.main.update_transcript(self._session.transcript)
        self.overlay.set_transcript(self._session.transcript)

    def _partial_failed(self, error: str, path: Path, generation: int, audio_generation: int) -> None:
        self._cleanup_audio(path)
        if generation == self._generation and audio_generation == self._audio_generation:
            self._partial_inflight = False
        LOG.warning("Streaming partial failed: %s", error)

    def _engine(self):
        if self._asr_engine is None:
            config = self.config.section("asr")
            provider = str(config.get("provider", "openai_compatible"))
            key = self.secrets.get(SecretStore.key_for("asr", provider))
            self._asr_engine = create_asr_engine(config, key)
        return self._asr_engine

    def _begin_compose(self) -> None:
        if not self._session or self._session.state != SessionState.COMPOSING:
            return
        self._compose_when_ready = False
        session = self._session
        # Settings may have been saved by another running/build instance. Always compose from
        # the authoritative on-disk configuration rather than a stale launch-time snapshot.
        self.config.reload()
        prompt_config = self.config.section("prompt")
        target = str(prompt_config.get("target", "codex_cli"))
        template = prompt_config.get("templates", {}).get(target, "")
        llm_config = self.config.section("llm")
        use_llm = bool(llm_config.get("enabled")) and llm_config.get("provider") not in {"rules", "disabled"}
        options = ComposeOptions(
            target=target,
            custom_template=template,
            use_llm=use_llm,
            preserve_chinese=bool(llm_config.get("preserve_chinese", True)),
            source_app=session.source_app,
        )

        def compose() -> tuple[str, str, dict[str, Any]]:
            warning = "\n".join(self._asr_warnings)
            client = None
            provider = str(llm_config.get("provider", "openai_compatible"))
            metadata: dict[str, Any] = {
                "requested": use_llm,
                "used": False,
                "provider": provider,
                "model": str(llm_config.get("model", "")),
            }
            if use_llm:
                key = self.secrets.get(SecretStore.key_for("llm", provider))
                client = LLMComposerClient(llm_config, key)
            started = time.perf_counter()
            try:
                prompt = self.composer.compose(session, options, client)
                if use_llm and client is not None:
                    metadata["used"] = bool(client.last_response_text)
                    metadata.update(client.last_metadata)
                    if not client.last_response_text:
                        metadata["fallback_reason"] = "empty_response"
                        warning = "\n".join(
                            filter(
                                None,
                                (warning, "LLM returned an empty task; rule composer was used."),
                            )
                        )
            except LLMError as exc:
                warning = "\n".join(filter(None, (warning, f"LLM rewrite failed; rule composer used: {exc}")))
                options.use_llm = False
                prompt = self.composer.compose(session, options)
                metadata["error"] = str(exc)
                metadata["fallback_reason"] = "llm_error"
            metadata["latency_ms"] = round((time.perf_counter() - started) * 1000)
            LOG.info(
                "Prompt composer completed: requested=%s used=%s provider=%s model=%s latency=%s ms request_id=%s",
                metadata["requested"],
                metadata["used"],
                metadata["provider"],
                metadata["model"],
                metadata["latency_ms"],
                metadata.get("request_id", ""),
            )
            return prompt, warning, metadata

        self._submit(compose, self._compose_done, self._fail_session)

    def _compose_done(self, result: tuple[str, str, dict[str, Any]]) -> None:
        if not self._session or self._session.state != SessionState.COMPOSING:
            return
        prompt, warning, metadata = result
        self._session.final_prompt = prompt
        self._session.composer_metadata = metadata
        self._session.transition(SessionState.PROMPT_READY)
        self.main.set_session(self._session)
        self._update_state_ui()
        self.main.set_composer_status(metadata, warning)
        output_config = self.config.section("output")
        if output_config.get("save_history", True) and self.config.get("privacy.local_logs", True):
            include_refs = bool(self.config.get("privacy.save_reference_content", True))
            self.history.save(self._session.to_dict(include_reference_content=include_refs))
            self._refresh_history()
        if output_config.get("copy_to_clipboard", True) or output_config.get("auto_paste", False):
            self.copy_prompt(prompt, quiet=True)
        message = "Prompt copied" if output_config.get("copy_to_clipboard", True) else "Prompt ready"
        if metadata.get("used"):
            model = metadata.get("response_model") or metadata.get("model") or metadata.get("provider")
            composer_type = "streaming LLM" if metadata.get("streamed") else "LLM"
            message += f" · {composer_type} {model}"
        elif metadata.get("requested"):
            message += " · rule fallback"
        else:
            message += " · rule composer"
        if warning:
            message += f"\n{warning[:220]}"
            self.tray.showMessage("VoiceContext warning", warning[:500], QSystemTrayIcon.MessageIcon.Warning, 5000)
        self.overlay.set_state("PromptReady", message)
        if output_config.get("show_preview", True):
            self.show_main()
        if output_config.get("auto_paste", False):
            self._automatic_paste()
        if self.config.get("clipboard.restore_original", False) and self._original_clipboard:
            # Keep the generated prompt available briefly for automatic paste, then restore the user's clipboard.
            session_id = self._session.id
            QTimer.singleShot(
                1800 if output_config.get("auto_paste", False) else 3500,
                lambda: self._restore_clipboard(session_id),
            )

    def regenerate(self) -> None:
        if not self._session or self._session.state != SessionState.PROMPT_READY:
            return
        self._session.transition(SessionState.COMPOSING)
        self._update_state_ui()
        self.overlay.set_state("Composing", "Regenerating prompt...")
        self._begin_compose()

    def _clipboard_captured(
        self, text: str, content_type: str, source_process: str, source_window: str, sensitive: bool
    ) -> None:
        if not self._session or self._session.state not in {SessionState.LISTENING, SessionState.PAUSED}:
            return
        reference = self._session.add_reference(
            text,
            source_process=source_process,
            source_window=source_window,
            content_type=content_type,
            dedupe=bool(self.config.get("clipboard.dedupe", True)),
        )
        if not reference:
            return
        self.main.update_references(self._session)
        self.overlay.set_references(self._session.references)
        should_warn = (
            sensitive
            and self.config.get("clipboard.sensitive_warning", True)
            and self.config.get("privacy.sensitive_detection", True)
        )
        if self.config.get("clipboard.show_toast", True):
            self.overlay.show_capture(reference, should_warn)
        if should_warn:
            self.tray.showMessage(
                "Sensitive clipboard content",
                f"Review [{reference.id}] before sending it to a remote service.",
                QSystemTrayIcon.MessageIcon.Warning,
                4000,
            )

    def delete_reference(self, reference_id: str) -> None:
        if self._session and self._session.remove_reference(reference_id):
            self.main.update_references(self._session)
            self.overlay.set_references(self._session.references)

    def update_reference(self, reference_id: str, name: str, content_type: str) -> None:
        if self._session and self._session.update_reference(reference_id, name, content_type):
            self.main.update_references(self._session)
            self.overlay.set_references(self._session.references)

    def undo_reference(self) -> None:
        if self._session and self._session.state in {SessionState.LISTENING, SessionState.PAUSED}:
            removed = self._session.undo_reference()
            if removed:
                self.main.update_references(self._session)
                self.overlay.set_references(self._session.references)
                self.overlay.show_capture(removed)
                self.overlay.status.setText(f"Removed {removed.id}")

    def ignore_reference(self) -> None:
        if self._session and self._session.state in {SessionState.LISTENING, SessionState.PAUSED}:
            removed = self._session.ignore_last_capture()
            if removed:
                self.main.update_references(self._session)
                self.overlay.set_references(self._session.references)
                self.overlay.show_capture(removed)
                self.overlay.status.setText("Capture ignored")

    def copy_prompt(self, text: str, quiet: bool = False) -> None:
        if not text.strip():
            return
        if self._session and self._session.state == SessionState.PROMPT_READY and text != self._session.final_prompt:
            self._session.final_prompt = text
            if self.config.get("output.save_history", True) and self.config.get("privacy.local_logs", True):
                self.history.save(
                    self._session.to_dict(
                        include_reference_content=bool(self.config.get("privacy.save_reference_content", True))
                    )
                )
        self.clipboard_watcher.suppress_next_change()
        self.output.copy(text)
        if not quiet:
            self.overlay.set_state("PromptReady", "Prompt copied to clipboard")

    def _restore_clipboard(self, session_id: str | None = None) -> None:
        if session_id and session_id != self._original_clipboard_session_id:
            return
        if not self._original_clipboard:
            return
        text, html = self._original_clipboard
        data = QMimeData()
        data.setText(text)
        if html:
            data.setHtml(html)
        self.clipboard_watcher.suppress_next_change()
        self.app.clipboard().setMimeData(data)
        self._original_clipboard = None
        self._original_clipboard_session_id = None

    def paste_prompt(self, text: str) -> None:
        if not text.strip():
            return
        self.copy_prompt(text, quiet=True)
        self.main.hide()
        activate_window(self._target_window.hwnd)
        QTimer.singleShot(180, lambda: self.output.paste(False))

    def _automatic_paste(self) -> None:
        if not self._session:
            return
        if self.config.get("output.confirm_before_paste", True):
            answer = QMessageBox.question(
                self.main,
                "Paste prompt",
                f"Paste the generated prompt into {self._target_window.process or 'the source window'}?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.main.hide()
        activate_window(self._target_window.hwnd)
        submit = bool(self.config.get("output.auto_submit", False))
        QTimer.singleShot(200, lambda: self.output.paste(submit))

    def recopy_last_prompt(self) -> None:
        prompt = self._session.final_prompt if self._session else ""
        if not prompt:
            rows = self.history.recent(1)
            prompt = rows[0].get("final_prompt", "") if rows else ""
        if prompt:
            self.copy_prompt(prompt)

    def export_prompt(self, text: str) -> None:
        if not text.strip():
            return
        session_id = self._session.id if self._session else "prompt"
        try:
            path = self.output.export_markdown(
                text, str(self.config.get("output.export_directory", "")), session_id
            )
            self.tray.showMessage("Prompt exported", str(path), QSystemTrayIcon.MessageIcon.Information, 3500)
        except OSError as exc:
            QMessageBox.critical(self.main, "Export failed", str(exc))

    def toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            state = self._session.state.value if self._session else "Idle"
            self.overlay.set_state(state, self._session.transcript if self._session else "Ready")

    def show_main(self) -> None:
        self.main.show_normal()

    def show_history(self) -> None:
        self._refresh_history()
        self.main.show_history_page()

    def show_settings(self) -> None:
        if self._settings_window and self._settings_window.isVisible():
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        # Prevent an existing global binding from firing while the user records it.
        self.hotkeys.unregister()
        window = SettingsWindow(self.config, self.secrets, self.main)
        self._settings_window = window
        window.saved.connect(self._settings_saved)
        window.clear_history_requested.connect(self._clear_history)
        window.test_asr_requested.connect(self._test_asr)
        window.test_llm_requested.connect(self._test_llm)
        window.finished.connect(self._settings_closed)
        window.show()

    def _settings_closed(self, _result: int) -> None:
        self._settings_window = None
        errors = self.hotkeys.register(self.config.section("hotkeys"))
        if errors:
            LOG.warning("Hotkey registration after closing settings: %s", "; ".join(errors))

    def _settings_saved(self) -> None:
        self._asr_engine = None
        self._apply_runtime_settings()
        self._refresh_history()

    def _test_asr(self, config: dict, key: str, path: str) -> None:
        window = self._settings_window or self.main

        def task():
            return create_asr_engine(config, key).transcribe(Path(path)).text

        self._submit(
            task,
            lambda text: QMessageBox.information(window, "ASR test succeeded", text or "The request succeeded but returned no speech."),
            lambda error: QMessageBox.critical(window, "ASR test failed", error),
        )

    def _test_llm(self, config: dict, key: str) -> None:
        window = self._settings_window or self.main

        def task():
            client = LLMComposerClient(config, key)
            ref = ReferenceEvent("ref_1", 1000, "example.py", "file")
            return client.rewrite("请检查这里的错误并给出修改", "speech then reference [ref_1]", [ref])

        self._submit(
            task,
            lambda text: QMessageBox.information(window, "LLM test succeeded", text or "The endpoint returned an empty task."),
            lambda error: QMessageBox.critical(window, "LLM test failed", error),
        )

    def _refresh_history(self) -> None:
        self.main.set_history(self.history.recent(100))

    def _load_history_prompt(self, session_id: str) -> None:
        data = self.history.get(session_id)
        self.main.show_history_prompt(data.get("finalPrompt", "") if data else "")

    def _delete_history(self, session_id: str) -> None:
        self.history.delete(session_id)
        self._refresh_history()

    def _clear_history(self) -> None:
        self.history.clear()
        self._refresh_history()

    def _update_elapsed(self) -> None:
        if self._session:
            self.overlay.set_elapsed(self._session.elapsed_ms())

    def _update_state_ui(self) -> None:
        state = self._session.state if self._session else SessionState.IDLE
        self.main.set_state(state)
        self.tray.set_state(state.value)

    def _show_hotkey_error(self, message: str) -> None:
        LOG.warning("Hotkey registration: %s", message)

    def _fail_session(self, message: str) -> None:
        LOG.error("Session failed: %s", message)
        self.partial_timer.stop()
        self.elapsed_timer.stop()
        self.clipboard_watcher.stop()
        if self._audio:
            self._audio.discard()
            self._audio = None
        engine, self._realtime_engine = self._realtime_engine, None
        self._realtime_closing = False
        if engine is not None:
            self.executor.submit(self._quiet_stop_realtime, engine)
        if self._session:
            self._session.error = message
            if self._session.state != SessionState.ERROR:
                try:
                    self._session.transition(SessionState.ERROR)
                except ValueError:
                    pass
            self.main.set_session(self._session)
        self._update_state_ui()
        self.overlay.set_state("Error", message)
        self.tray.showMessage("VoiceContext error", message[:500], QSystemTrayIcon.MessageIcon.Critical, 6000)

    @staticmethod
    def _quiet_stop_realtime(engine: AliyunRealtimeASR) -> None:
        try:
            engine.stop()
        except Exception:
            LOG.debug("Realtime ASR cleanup failed", exc_info=True)

    def _submit(
        self,
        operation: Callable[[], Any],
        success: Callable[[Any], None],
        failure: Callable[[str], None],
    ) -> None:
        signals = TaskSignals()
        self._tasks.add(signals)
        signals.succeeded.connect(success)
        signals.failed.connect(failure)

        def finish_task(_: Any = None) -> None:
            self._tasks.discard(signals)

        signals.succeeded.connect(finish_task)
        signals.failed.connect(finish_task)
        future = self.executor.submit(operation)

        def done(completed: Future) -> None:
            try:
                signals.succeeded.emit(completed.result())
            except Exception as exc:
                LOG.exception("Background task failed")
                signals.failed.emit(str(exc))

        future.add_done_callback(done)

    def _cleanup_audio(self, path: Path) -> None:
        if self.config.get("advanced.keep_audio", False):
            archive = str(self.config.get("advanced.audio_directory", ""))
            if archive:
                try:
                    target_dir = Path(archive).expanduser()
                    target_dir.mkdir(parents=True, exist_ok=True)
                    path.replace(target_dir / path.name)
                    return
                except OSError as exc:
                    LOG.warning("Could not archive audio: %s", exc)
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def shutdown(self) -> None:
        self.hotkeys.unregister()
        self.partial_timer.stop()
        self.elapsed_timer.stop()
        self.clipboard_watcher.stop()
        if self._audio:
            self._audio.discard()
        if self._realtime_engine:
            self._quiet_stop_realtime(self._realtime_engine)
            self._realtime_engine = None
        self.tray.hide()
        self.overlay.close()
        self.main.shutdown()
        self.executor.shutdown(wait=False, cancel_futures=True)


def configure_logging(config: ConfigStore) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    level_name = str(config.get("advanced.log_level", "INFO"))
    if config.get("privacy.debug_logging", False):
        level_name = "DEBUG"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.get("privacy.local_logs", True):
        handlers.append(logging.FileHandler(APP_DIR / "voicecontext.log", encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def run_asr_self_test(arguments: list[str]) -> int:
    """Run the configured ASR engine without Qt and write a machine-readable diagnostic result."""
    try:
        audio_index = arguments.index("--asr-self-test") + 1
        audio_path = Path(arguments[audio_index]).expanduser().resolve()
    except (ValueError, IndexError):
        return 2
    if "--result-file" in arguments:
        try:
            result_path = Path(arguments[arguments.index("--result-file") + 1]).expanduser().resolve()
        except IndexError:
            return 2
    else:
        result_path = APP_DIR / "asr-self-test.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any]
    exit_code = 0
    try:
        config = ConfigStore().section("asr")
        provider = str(config.get("provider", "openai_compatible"))
        key = SecretStore().get(SecretStore.key_for("asr", provider))
        result = create_asr_engine(config, key).transcribe(audio_path)
        payload = {
            "ok": True,
            "provider": provider,
            "text": result.text,
            "language": result.language,
            "duration_s": result.duration_s,
            "segments": result.segments,
        }
    except Exception as exc:
        exit_code = 1
        payload = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return exit_code


def main() -> int:
    if "--asr-self-test" in sys.argv:
        return run_asr_self_test(sys.argv[1:])
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shell32.SetCurrentProcessExplicitAppUserModelID("VoiceContext.Desktop")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("VoiceContext")
    app.setOrganizationName("VoiceContext")
    config = ConfigStore()
    configure_logging(config)
    instance = SingleInstance.acquire()
    if instance is None:
        return 0
    controller = VoiceContextController(app, instance)
    app._voicecontext_controller = controller  # type: ignore[attr-defined]
    return app.exec()
