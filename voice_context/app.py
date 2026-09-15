from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QMimeData, QObject, Qt, QTimer, Signal
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
from voice_context.output.dispatcher import ClipboardWriteResult, OutputDispatcher
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


@dataclass(slots=True)
class PromptComposeResult:
    prompt: str
    warning: str
    metadata: dict[str, Any]
    error: str = ""


@dataclass(slots=True)
class CaptureStartupResult:
    audio: AudioCapture
    realtime_engine: AliyunRealtimeASR | None = None


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class SingleInstance(QObject):
    message_received = Signal(str)
    NAME = "VoiceContext-6A45E04B-5B19-4B4B-A3D8"
    last_error = ""

    @classmethod
    def acquire(cls) -> SingleInstance | None:
        cls.last_error = ""
        probe = QLocalSocket()
        probe.connectToServer(cls.NAME)
        if probe.waitForConnected(150):
            probe.write(b"show")
            probe.waitForBytesWritten(150)
            if not probe.waitForReadyRead(300):
                LOG.warning("The running VoiceContext instance did not acknowledge the show request.")
            probe.disconnectFromServer()
            return None
        if probe.error() not in {
            QLocalSocket.LocalSocketError.ServerNotFoundError,
            QLocalSocket.LocalSocketError.ConnectionRefusedError,
        }:
            cls.last_error = f"Could not contact the running VoiceContext instance: {probe.errorString()}"
            LOG.error(cls.last_error)
            return None
        QLocalServer.removeServer(cls.NAME)
        instance = cls()
        instance.server = QLocalServer(instance)
        instance._sockets: set[QLocalSocket] = set()
        if not instance.server.listen(cls.NAME):
            cls.last_error = (
                "Could not create the VoiceContext single-instance server: "
                f"{instance.server.errorString()}"
            )
            LOG.error(cls.last_error)
            return None
        instance.server.newConnection.connect(instance._accept)
        return instance

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self._sockets.add(socket)
            socket.readyRead.connect(lambda current=socket: self._read_socket(current))
            socket.disconnected.connect(
                lambda current=socket: self._sockets.discard(current)
            )
            if socket.bytesAvailable():
                self._read_socket(socket)

    def _read_socket(self, socket: QLocalSocket) -> None:
        message = bytes(socket.readAll()).decode("utf-8", "replace") or "show"
        LOG.info("Single-instance request received: %s", message)
        self.message_received.emit(message)
        socket.write(b"ok")
        socket.flush()
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
        self._finish_when_ready = False
        self._resume_inflight = False
        self._busy_notice_at = 0.0
        self._settings_window: SettingsWindow | None = None

        apply_theme(app, str(self.config.get("general.theme", "system")))
        app.setWindowIcon(create_tray_icon())
        app.setQuitOnLastWindowClosed(False)
        self.main = MainWindow()
        self.overlay = OverlayWindow(self.config.section("general"))
        self.tray = TrayIcon(self)
        self.hotkeys = HotkeyManager(window_handle=int(self.main.winId()))
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
        self.overlay.edit_requested.connect(self._edit_prompt_from_overlay)

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
        self.hotkeys.registration_changed.connect(self._hotkey_registration_changed)
        self.hotkeys.action_triggered.connect(self._log_hotkey_action)
        self.clipboard_watcher.captured.connect(self._clipboard_captured)

    def _apply_runtime_settings(self, initial: bool = False) -> None:
        apply_theme(self.app, str(self.config.get("general.theme", "system")))
        self.overlay.config = self.config.section("general")
        self.overlay.setWindowOpacity(float(self.config.get("general.overlay_opacity", 0.94)))
        self.clipboard_watcher.config = self.config.section("clipboard")
        self.hotkeys.register(self.config.section("hotkeys"))
        try:
            set_startup(bool(self.config.get("general.startup", False)))
        except OSError as exc:
            LOG.warning("Could not update startup registration: %s", exc)
        if self.config.get("privacy.auto_cleanup", True):
            self.history.cleanup(int(self.config.get("general.history_days", 30)))

    @property
    def active(self) -> bool:
        return bool(
            self._session
            and self._session.state
            in {
                SessionState.ARMED,
                SessionState.LISTENING,
                SessionState.PAUSED,
                SessionState.COMPOSING,
            }
        )

    def _uses_realtime_asr(self) -> bool:
        return self.config.get("asr.provider") == "aliyun_realtime"

    def start_session(self) -> None:
        if self.active:
            self._notify_busy()
            return
        if self._session and self._session.state == SessionState.ERROR:
            self._session.transition(SessionState.IDLE)
        self._generation += 1
        self._target_window = get_foreground_info()
        self._original_clipboard = None
        if self.config.get("clipboard.restore_original", False):
            try:
                mime = self.app.clipboard().mimeData()
                self._original_clipboard = (
                    self.app.clipboard().text(),
                    mime.html() if mime is not None and mime.hasHtml() else "",
                )
            except Exception as exc:
                LOG.warning("Could not capture the original clipboard: %s", exc)
        self._session = VoiceSession(source_app=self._target_window.process)
        asr_config = self.config.section("asr")
        self._session.asr_metadata = {
            "status": "listening",
            "provider": str(asr_config.get("provider", "")),
            "model": str(asr_config.get("model", "")),
            "streaming": self._uses_realtime_asr(),
            "segment_count": 0,
        }
        self._original_clipboard_session_id = self._session.id
        self._pending_segments = 0
        self._compose_when_ready = False
        self._asr_warnings.clear()
        self._partial_inflight = False
        self._asr_engine = None
        self._finish_when_ready = False
        self._session.transition(SessionState.ARMED)
        self._segment_start_ms = 0
        session_id = self._session.id
        generation = self._generation
        self.main.set_session(self._session)
        self._update_state_ui()
        self.overlay.set_state("Armed", "Starting microphone and speech recognition...")
        self._submit(
            self._create_capture_resources,
            lambda result: self._capture_started(result, session_id, generation),
            lambda error: self._capture_start_failed(error, session_id, generation),
        )

    def _create_capture_resources(
        self, use_realtime: bool | None = None
    ) -> CaptureStartupResult:
        config = self.config.section("asr")
        audio_config = dict(config)
        audio_config.update(self.config.section("advanced"))
        engine: AliyunRealtimeASR | None = None
        audio: AudioCapture | None = None
        if use_realtime is None:
            use_realtime = self._uses_realtime_asr()
        try:
            if use_realtime:
                key = self.secrets.get(SecretStore.key_for("asr", "aliyun_realtime"))
                engine = AliyunRealtimeASR(config, key)
                engine.start()
            audio = AudioCapture(audio_config)
            if engine is not None:
                audio.set_chunk_consumer(engine.send_audio)
            audio.start()
            target_thread = self.thread()
            audio.moveToThread(target_thread)
            if engine is not None:
                engine.moveToThread(target_thread)
            return CaptureStartupResult(audio, engine)
        except Exception:
            if audio is not None:
                audio.discard()
            if engine is not None:
                self._quiet_stop_realtime(engine)
            raise

    def _capture_started(
        self,
        result: CaptureStartupResult,
        session_id: str,
        generation: int,
    ) -> None:
        if (
            generation != self._generation
            or not self._session
            or self._session.id != session_id
            or self._session.state != SessionState.ARMED
        ):
            self.executor.submit(self._cleanup_capture_resources, result)
            return
        self._attach_capture_resources(result, session_id)
        self._session.transition(SessionState.LISTENING)
        self._segment_start_ms = self._session.elapsed_ms()
        self.clipboard_watcher.start()
        interval = max(800, int(float(self.config.get("asr.partial_interval_s", 2.5)) * 1000))
        if not self._uses_realtime_asr() and self.config.get("asr.streaming_partial", True):
            self.partial_timer.start(interval)
        self.elapsed_timer.start()
        self.main.set_session(self._session)
        self._update_state_ui()
        self.overlay.set_session_timeline(self._session)
        self.overlay.set_state("Listening")
        if self._finish_when_ready:
            self._finish_when_ready = False
            QTimer.singleShot(0, self.finish_session)

    def _attach_capture_resources(
        self, result: CaptureStartupResult, session_id: str
    ) -> None:
        self._audio_generation += 1
        self._audio = result.audio
        self._realtime_engine = result.realtime_engine
        self._realtime_closing = False
        self._audio.level_changed.connect(self.overlay.set_level)
        self._audio.error.connect(self._audio_error)
        if self._realtime_engine is not None:
            engine = self._realtime_engine
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
        else:
            self._audio.silence_detected.connect(self._silence_split)

    def _capture_start_failed(self, error: str, session_id: str, generation: int) -> None:
        if (
            generation == self._generation
            and self._session
            and self._session.id == session_id
            and self._session.state == SessionState.ARMED
        ):
            self._fail_session(f"Could not start listening: {error}")

    @staticmethod
    def _cleanup_capture_resources(result: CaptureStartupResult) -> None:
        result.audio.discard()
        if result.realtime_engine is not None:
            VoiceContextController._quiet_stop_realtime(result.realtime_engine)

    def _notify_busy(self) -> None:
        if not self._session or self._session.state not in {SessionState.ARMED, SessionState.COMPOSING}:
            return
        now = time.monotonic()
        if now - self._busy_notice_at < 0.8:
            return
        self._busy_notice_at = now
        detail = (
            "Starting the microphone..."
            if self._session.state == SessionState.ARMED
            else "A prompt is being generated. Use the cancel shortcut to discard it."
        )
        self.overlay.set_state(self._session.state.value, detail)

    def _audio_error(self, message: str) -> None:
        if message.startswith("Realtime audio delivery failed"):
            self._fail_session(message)
        else:
            LOG.warning("Audio stream: %s", message)

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
        self.overlay.set_session_timeline(self._session)

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
        self._stop_and_submit_segment(restart_after=True)

    def toggle_session(self) -> None:
        if self._session and self._session.state in {SessionState.LISTENING, SessionState.PAUSED}:
            self.finish_session()
        elif not self.active:
            self.start_session()
        else:
            self._notify_busy()

    def _hold_start(self) -> None:
        self._hold_owns_session = not self.active
        if self._hold_owns_session:
            self.start_session()
        else:
            self._notify_busy()

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
            self.overlay.set_session_timeline(self._session)
            self.overlay.set_state("Paused")
        elif self._session.state == SessionState.PAUSED:
            if self._resume_inflight:
                self.overlay.set_state("Paused", "Restarting the microphone...")
                return
            if self._uses_realtime_asr() and self._realtime_closing:
                self.overlay.set_state("Paused", "Finalizing the previous speech segment...")
                return
            self._resume_inflight = True
            session_id = self._session.id
            generation = self._generation
            self.overlay.set_state("Paused", "Restarting the microphone...")
            self._submit(
                self._create_capture_resources,
                lambda result: self._capture_resumed(result, session_id, generation),
                lambda error: self._capture_resume_failed(error, session_id, generation),
            )
        self._update_state_ui()

    def _capture_resumed(
        self, result: CaptureStartupResult, session_id: str, generation: int
    ) -> None:
        if (
            generation != self._generation
            or not self._session
            or self._session.id != session_id
            or self._session.state != SessionState.PAUSED
        ):
            self.executor.submit(self._cleanup_capture_resources, result)
            return
        self._resume_inflight = False
        self._attach_capture_resources(result, session_id)
        self._session.transition(SessionState.LISTENING)
        self._segment_start_ms = self._session.elapsed_ms()
        if not self._uses_realtime_asr() and self.config.get("asr.streaming_partial", True):
            self.partial_timer.start(
                max(800, int(float(self.config.get("asr.partial_interval_s", 2.5)) * 1000))
            )
        self._update_state_ui()
        self.overlay.set_session_timeline(self._session)
        self.overlay.set_state("Listening")

    def _capture_resume_failed(self, error: str, session_id: str, generation: int) -> None:
        if (
            generation == self._generation
            and self._session
            and self._session.id == session_id
            and self._session.state == SessionState.PAUSED
        ):
            self._resume_inflight = False
            self._fail_session(f"Could not resume the microphone: {error}")

    def finish_session(self) -> None:
        if self._session and self._session.state == SessionState.ARMED:
            self._finish_when_ready = True
            self.overlay.set_state("Armed", "Finishing as soon as the microphone is ready...")
            return
        if not self._session or self._session.state not in {SessionState.LISTENING, SessionState.PAUSED}:
            self._notify_busy()
            return
        self._generation += 1
        self._resume_inflight = False
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
        self._resume_inflight = False
        self.partial_timer.stop()
        self.elapsed_timer.stop()
        self.clipboard_watcher.stop()
        audio, self._audio = self._audio, None
        engine, self._realtime_engine = self._realtime_engine, None
        if audio is not None or engine is not None:
            def cleanup_cancelled_capture() -> None:
                if audio is not None:
                    audio.discard()
                if engine is not None:
                    self._quiet_stop_realtime(engine)

            self.executor.submit(cleanup_cancelled_capture)
        self._realtime_closing = False
        self._session = VoiceSession()
        self.main.set_session(self._session)
        self._update_state_ui()
        self.overlay.set_state("Idle", "Session cancelled")

    def _stop_realtime_capture(self) -> None:
        audio, self._audio = self._audio, None
        engine, self._realtime_engine = self._realtime_engine, None
        if engine is None:
            if audio is not None:
                self.executor.submit(audio.discard)
            return
        self._pending_segments += 1
        self._realtime_closing = True
        session_id = self._session.id if self._session else ""
        start_ms = self._segment_start_ms

        def finalize_realtime() -> RealtimeRecognitionResult:
            if audio is not None:
                audio.discard()
            return engine.stop()

        self._submit(
            finalize_realtime,
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
        self.overlay.set_session_timeline(self._session)
        self._session.asr_metadata.update(
            {
                "status": "success",
                "request_id": result.request_id,
                "first_package_delay_ms": round(result.first_package_delay_ms),
                "last_package_delay_ms": round(result.last_package_delay_ms),
                "segment_count": int(self._session.asr_metadata.get("segment_count", 0))
                + len(segments),
            }
        )
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

    def _stop_and_submit_segment(self, restart_after: bool = False) -> None:
        if not self._audio:
            return
        audio = self._audio
        self._audio = None
        end_ms = self._session.elapsed_ms() if self._session else self._segment_start_ms
        start_ms = self._segment_start_ms
        self._pending_segments += 1
        session_id = self._session.id if self._session else ""

        def finalize_audio():
            path: Path | None = None
            duration_ms = 0
            try:
                path = audio.stop()
                duration_ms = audio.duration_ms
                return path, duration_ms, ""
            except Exception as exc:
                return path, duration_ms, str(exc)

        self._submit(
            finalize_audio,
            lambda payload: self._segment_audio_finalized(
                payload,
                start_ms,
                end_ms,
                session_id,
                restart_after,
            ),
            lambda error: self._segment_audio_finalize_failed(
                error, session_id, restart_after
            ),
        )

    def _segment_audio_finalized(
        self,
        payload: tuple[Path | None, int, str],
        start_ms: int,
        end_ms: int,
        session_id: str,
        restart_after: bool,
    ) -> None:
        path, duration_ms, error = payload
        if not self._session or self._session.id != session_id:
            if path is not None:
                self._cleanup_audio(path)
            return
        if restart_after and self._session.state == SessionState.LISTENING:
            self._restart_capture_after_silence(session_id)
        if error:
            if path is not None:
                self._cleanup_audio(path)
            self._pending_segments = max(0, self._pending_segments - 1)
            self._asr_warnings.append(error)
            self._session.asr_metadata.update({"status": "failed", "error": error})
            if self._compose_when_ready and self._pending_segments == 0:
                self._begin_compose()
            return
        if path is None or duration_ms < 120:
            if path is not None:
                self._cleanup_audio(path)
            self._pending_segments = max(0, self._pending_segments - 1)
            if self._compose_when_ready and self._pending_segments == 0:
                self._begin_compose()
            return

        def transcribe():
            started = time.perf_counter()
            with self._asr_lock:
                result = self._engine().transcribe(path)
            return result, round((time.perf_counter() - started) * 1000)

        self._submit(
            transcribe,
            lambda result: self._segment_transcribed(
                result, start_ms, end_ms, path, session_id
            ),
            lambda message: self._segment_transcription_failed(
                message, path, session_id
            ),
        )

    def _restart_capture_after_silence(self, session_id: str) -> None:
        generation = self._generation
        self._submit(
            lambda: self._create_capture_resources(False),
            lambda result: self._capture_restarted_after_silence(
                result, session_id, generation
            ),
            lambda error: self._capture_restart_failed(
                error, session_id, generation
            ),
        )

    def _capture_restarted_after_silence(
        self,
        result: CaptureStartupResult,
        session_id: str,
        generation: int,
    ) -> None:
        if (
            generation != self._generation
            or not self._session
            or self._session.id != session_id
            or self._session.state != SessionState.LISTENING
            or self._audio is not None
        ):
            self.executor.submit(self._cleanup_capture_resources, result)
            return
        self._attach_capture_resources(result, session_id)
        self._segment_start_ms = self._session.elapsed_ms()

    def _capture_restart_failed(
        self, error: str, session_id: str, generation: int
    ) -> None:
        if (
            generation == self._generation
            and self._session
            and self._session.id == session_id
            and self._session.state == SessionState.LISTENING
        ):
            self._fail_session(f"Could not continue after silence: {error}")

    def _segment_transcribed(
        self,
        payload: tuple[Any, int],
        start_ms: int,
        end_ms: int,
        path: Path,
        session_id: str,
    ) -> None:
        result, latency_ms = payload
        self._cleanup_audio(path)
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        if result.text.strip():
            self._session.add_speech(result.text, start_ms, end_ms, is_final=True)
            self.main.update_transcript(self._session.transcript)
            self.overlay.set_session_timeline(self._session)
        self._session.asr_metadata.update(
            {
                "status": "success",
                "latency_ms": int(self._session.asr_metadata.get("latency_ms", 0))
                + latency_ms,
                "segment_count": int(self._session.asr_metadata.get("segment_count", 0)) + 1,
            }
        )
        if self._compose_when_ready and self._pending_segments == 0:
            self._begin_compose()

    def _segment_audio_finalize_failed(
        self, error: str, session_id: str, restart_after: bool
    ) -> None:
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        self._asr_warnings.append(error)
        if restart_after and self._session.state == SessionState.LISTENING:
            self._restart_capture_after_silence(session_id)
        if self._compose_when_ready and self._pending_segments == 0:
            self._begin_compose()

    def _segment_transcription_failed(
        self, error: str, path: Path, session_id: str
    ) -> None:
        self._cleanup_audio(path)
        if not self._session or self._session.id != session_id:
            return
        self._pending_segments = max(0, self._pending_segments - 1)
        self._asr_warnings.append(error)
        self._session.asr_metadata.update({"status": "failed", "error": error})
        if self._compose_when_ready and self._pending_segments == 0:
            self._begin_compose()

    def _request_partial(self) -> None:
        if (
            self._partial_inflight
            or not self._audio
            or not self._session
            or self._session.state != SessionState.LISTENING
            or self._audio.duration_ms < 500
        ):
            return
        self._partial_inflight = True
        generation = self._generation
        audio_generation = self._audio_generation
        audio = self._audio

        def snapshot_and_transcribe():
            path: Path | None = None
            try:
                path = audio.snapshot()
                with self._asr_lock:
                    result = self._engine().transcribe(path)
                return result, path, ""
            except Exception as exc:
                return None, path, str(exc)

        self._submit(
            snapshot_and_transcribe,
            lambda payload: self._partial_snapshot_done(
                payload, generation, audio_generation
            ),
            lambda error: self._partial_task_failed(error, generation, audio_generation),
        )

    def _partial_snapshot_done(
        self,
        payload: tuple[Any, Path | None, str],
        generation: int,
        audio_generation: int,
    ) -> None:
        result, path, error = payload
        if path is not None:
            self._cleanup_audio(path)
        if generation == self._generation and audio_generation == self._audio_generation:
            self._partial_inflight = False
        if error:
            LOG.warning("Streaming partial failed: %s", error)
            return
        if (
            generation != self._generation
            or audio_generation != self._audio_generation
            or not self._session
            or self._session.state != SessionState.LISTENING
        ):
            return
        self._session.set_partial(result.text)
        self.main.update_transcript(self._session.transcript)
        self.overlay.set_session_timeline(self._session)

    def _partial_task_failed(
        self, error: str, generation: int, audio_generation: int
    ) -> None:
        if generation == self._generation and audio_generation == self._audio_generation:
            self._partial_inflight = False
        LOG.warning("Streaming partial task failed: %s", error)

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
        direct_transcript = not session.references and bool(session.transcript.strip())
        llm_configured = bool(llm_config.get("enabled")) and llm_config.get(
            "provider"
        ) not in {"rules", "disabled"}
        if not session.references and not session.transcript.strip():
            # Nothing was spoken or copied: rewriting cannot add anything, so fail
            # with a clear reason instead of sending an empty request to the LLM.
            metadata: dict[str, Any] = {
                "requested": False,
                "used": False,
                "provider": str(llm_config.get("provider", "openai_compatible")),
                "model": str(llm_config.get("model", "")),
                "mode": "empty_session",
                "skip_reason": "no_speech_and_no_references",
            }
            LOG.info("Skipping composition because nothing was recognized or captured")
            result = PromptComposeResult("", "", metadata, "No speech was recognized.")
            QTimer.singleShot(0, lambda: self._compose_done(result))
            return
        use_llm = llm_configured and not direct_transcript
        options = ComposeOptions(
            target=target,
            custom_template=template,
            use_llm=use_llm,
            preserve_chinese=bool(llm_config.get("preserve_chinese", True)),
            source_app=session.source_app,
        )

        def compose() -> PromptComposeResult:
            warning = "\n".join(self._asr_warnings)
            client = None
            provider = str(llm_config.get("provider", "openai_compatible"))
            metadata: dict[str, Any] = {
                "requested": use_llm,
                "used": False,
                "provider": provider,
                "model": str(llm_config.get("model", "")),
            }
            if direct_transcript:
                metadata.update(
                    {
                        "mode": "direct_transcript",
                        "skip_reason": "no_references",
                        "llm_configured": llm_configured,
                    }
                )
                LOG.info(
                    "Using the ASR transcript directly because the session has no references"
                )
                return PromptComposeResult(session.transcript.strip(), warning, metadata)
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
                        raise LLMError("LLM returned an empty task.")
            except LLMError as exc:
                metadata["error"] = str(exc)
                metadata["failure_reason"] = (
                    "empty_response" if "empty task" in str(exc).lower() else "llm_error"
                )
                metadata["latency_ms"] = round((time.perf_counter() - started) * 1000)
                LOG.warning(
                    "Prompt composer failed: provider=%s model=%s latency=%s ms error=%s",
                    metadata["provider"],
                    metadata["model"],
                    metadata["latency_ms"],
                    exc,
                )
                return PromptComposeResult("", warning, metadata, str(exc))
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
            return PromptComposeResult(prompt, warning, metadata)

        self._submit(compose, self._compose_done, self._fail_session)

    def _compose_done(self, result: PromptComposeResult) -> None:
        if not self._session or self._session.state != SessionState.COMPOSING:
            return
        if result.error:
            self._compose_failed(result)
            return
        self._session.final_prompt = result.prompt
        self._session.composer_metadata = result.metadata
        self._session.output_metadata = {"status": "pending"}
        self._session.transition(SessionState.PROMPT_READY)
        self.main.set_session(self._session)
        self._update_state_ui()
        output_config = self.config.section("output")
        copy_requested = bool(
            output_config.get("copy_to_clipboard", True) or output_config.get("auto_paste", False)
        )
        session_id = self._session.id
        if copy_requested:
            self.output.copy_verified(
                result.prompt,
                lambda copy_result: self._prompt_delivery_done(
                    session_id,
                    copy_result,
                    copy_requested=True,
                    warning=result.warning,
                ),
            )
        else:
            self._prompt_delivery_done(
                session_id,
                ClipboardWriteResult(False, 0),
                copy_requested=False,
                warning=result.warning,
            )

    def _compose_failed(self, result: PromptComposeResult) -> None:
        if not self._session:
            return
        self._session.final_prompt = ""
        self._session.composer_metadata = result.metadata
        self._session.output_metadata = {
            "clipboard_requested": False,
            "clipboard_verified": False,
            "attempts": 0,
            "status": "skipped",
        }
        self._session.error = result.error
        self._session.transition(SessionState.ERROR)
        self.main.set_session(self._session)
        self._update_state_ui()
        self.main.set_completion_receipt(self._session, result.warning)
        self._save_session()
        self.overlay.show_compose_failure(result.error, len(self._session.references))
        self.tray.showMessage(
            "VoiceContext generation failed",
            result.error[:500],
            QSystemTrayIcon.MessageIcon.Critical,
            5000,
        )

    def _prompt_delivery_done(
        self,
        session_id: str,
        result: ClipboardWriteResult,
        *,
        copy_requested: bool,
        warning: str = "",
    ) -> None:
        if (
            not self._session
            or self._session.id != session_id
            or self._session.state != SessionState.PROMPT_READY
        ):
            return
        clipboard_state = "not_requested"
        if copy_requested:
            clipboard_state = "copied" if result.success else "failed"
        restore_original = bool(
            result.success
            and self.config.get("clipboard.restore_original", False)
            and self._original_clipboard
        )
        self._session.output_metadata = {
            "clipboard_requested": copy_requested,
            "clipboard_verified": bool(copy_requested and result.success),
            "attempts": result.attempts,
            "status": clipboard_state,
            "error": result.error,
            "restored_original_pending": restore_original,
        }
        self.main.set_completion_receipt(self._session, warning)
        self._save_session()
        self.overlay.show_completion(
            self._session.final_prompt,
            len(self._session.references),
            clipboard_state,
            restore_original=restore_original,
        )
        if warning:
            self.tray.showMessage(
                "VoiceContext warning",
                warning[:500],
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
        if copy_requested and not result.success:
            self.tray.showMessage(
                "Clipboard copy failed",
                result.error or "The generated prompt is still available in VoiceContext.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            return
        output_config = self.config.section("output")
        if output_config.get("auto_paste", False) and result.success:
            self._automatic_paste()
        if restore_original:
            QTimer.singleShot(
                1800 if output_config.get("auto_paste", False) else 3500,
                lambda: self._restore_clipboard(session_id),
            )

    def _save_session(self) -> None:
        if not self._session:
            return
        if self.config.get("output.save_history", True) and self.config.get(
            "privacy.local_logs", True
        ):
            include_refs = bool(self.config.get("privacy.save_reference_content", True))
            self.history.save(self._session.to_dict(include_reference_content=include_refs))
            self._refresh_history()

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
        self.overlay.set_session_timeline(self._session, highlighted_id=reference.id)
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
            self.overlay.set_session_timeline(self._session)

    def update_reference(self, reference_id: str, name: str, content_type: str) -> None:
        if self._session and self._session.update_reference(reference_id, name, content_type):
            self.main.update_references(self._session)
            self.overlay.set_session_timeline(self._session)

    def undo_reference(self) -> None:
        if self._session and self._session.state in {SessionState.LISTENING, SessionState.PAUSED}:
            removed = self._session.undo_reference()
            if removed:
                self.main.update_references(self._session)
                self.overlay.set_session_timeline(self._session)
                self.overlay.show_capture(removed)
                self.overlay.status.setText(f"Removed {removed.id}")

    def ignore_reference(self) -> None:
        if self._session and self._session.state in {SessionState.LISTENING, SessionState.PAUSED}:
            removed = self._session.ignore_last_capture()
            if removed:
                self.main.update_references(self._session)
                self.overlay.set_session_timeline(self._session)
                self.overlay.show_capture(removed)
                self.overlay.status.setText("Capture ignored")

    def copy_prompt(
        self,
        text: str,
        quiet: bool = False,
        on_complete: Callable[[ClipboardWriteResult], None] | None = None,
    ) -> None:
        if not text.strip():
            return
        editable_session = (
            self._session
            if self._session and self._session.state == SessionState.PROMPT_READY
            else None
        )
        if editable_session and text != editable_session.final_prompt:
            self._session.final_prompt = text
            self._session.output_metadata = {"status": "pending"}
            self._save_session()
        self.clipboard_watcher.suppress_next_change()
        session_id = editable_session.id if editable_session else ""

        def done(result: ClipboardWriteResult) -> None:
            if (
                self._session
                and self._session.id == session_id
                and self._session.state == SessionState.PROMPT_READY
            ):
                self._session.output_metadata = {
                    "clipboard_requested": True,
                    "clipboard_verified": result.success,
                    "attempts": result.attempts,
                    "status": "copied" if result.success else "failed",
                    "error": result.error,
                }
                self.main.set_completion_receipt(self._session)
                self._save_session()
                if not quiet:
                    self.overlay.show_completion(
                        self._session.final_prompt,
                        len(self._session.references),
                        "copied" if result.success else "failed",
                    )
            if not result.success:
                self.tray.showMessage(
                    "Clipboard copy failed",
                    result.error,
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000,
                )
            if on_complete:
                on_complete(result)

        self.output.copy_verified(text, done)

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
        if self._session and (not session_id or self._session.id == session_id):
            self._session.output_metadata.update(
                {
                    "restored_original_pending": False,
                    "restored_original": True,
                }
            )
            self.main.set_completion_receipt(self._session)
            self._save_session()
        self._original_clipboard = None
        self._original_clipboard_session_id = None

    def paste_prompt(self, text: str) -> None:
        if not text.strip():
            return

        def paste_after_copy(result: ClipboardWriteResult) -> None:
            if not result.success:
                return
            self.main.hide()
            activate_window(self._target_window.hwnd)
            QTimer.singleShot(180, lambda: self.output.paste(False))

        self.copy_prompt(text, quiet=True, on_complete=paste_after_copy)

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
            if self._session and self._session.state == SessionState.PROMPT_READY:
                output = self._session.output_metadata
                self.overlay.show_completion(
                    self._session.final_prompt,
                    len(self._session.references),
                    str(output.get("status", "not_requested")),
                    restore_original=bool(output.get("restored_original_pending", False)),
                )
            else:
                if self._session:
                    self.overlay.set_session_timeline(self._session)
                    self.overlay.set_state(state)
                else:
                    self.overlay.set_state(state, "Ready")

    def _edit_prompt_from_overlay(self) -> None:
        if not self._session or self._session.state != SessionState.PROMPT_READY:
            return
        self.main.set_session(self._session)
        self.main.set_completion_receipt(self._session)
        self.main.focus_prompt_editor()

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
        window = SettingsWindow(self.config, self.secrets, self.main)
        self._settings_window = window
        window.saved.connect(self._settings_saved)
        window.clear_history_requested.connect(self._clear_history)
        window.test_asr_requested.connect(self._test_asr)
        window.test_llm_requested.connect(self._test_llm)
        window.hotkeys_page_entered.connect(self.hotkeys.suspend)
        window.hotkeys_page_exited.connect(self.hotkeys.resume)
        window.finished.connect(self._settings_closed)
        window.show()

    def _settings_closed(self, _result: int) -> None:
        self._settings_window = None
        self.hotkeys.resume()

    def _settings_saved(self) -> None:
        self._asr_engine = None
        self._apply_runtime_settings()
        self._refresh_history()

    def _test_asr(self, config: dict, key: str, path: str) -> None:
        window = self._settings_window or self.main

        def task():
            return create_asr_engine(config, key).transcribe(Path(path)).text

        def on_success(text):
            if self._settings_window:
                self._settings_window.restore_test_buttons()
            QMessageBox.information(window, "ASR test succeeded", text or "The request succeeded but returned no speech.")

        def on_error(error):
            if self._settings_window:
                self._settings_window.restore_test_buttons()
            QMessageBox.critical(window, "ASR test failed", error)

        self._submit(task, on_success, on_error)

    def _test_llm(self, config: dict, key: str) -> None:
        window = self._settings_window or self.main

        def task():
            client = LLMComposerClient(config, key)
            ref = ReferenceEvent("ref_1", 1000, "example.py", "file")
            return client.rewrite("请检查这里的错误并给出修改", "speech then reference [ref_1]", [ref])

        def on_success(text):
            if self._settings_window:
                self._settings_window.restore_test_buttons()
            QMessageBox.information(window, "LLM test succeeded", text or "The endpoint returned an empty task.")

        def on_error(error):
            if self._settings_window:
                self._settings_window.restore_test_buttons()
            QMessageBox.critical(window, "LLM test failed", error)

        self._submit(task, on_success, on_error)

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
        self.main.statusBar().showMessage(f"Global hotkey issue: {message}")
        QTimer.singleShot(
            0,
            lambda detail=message: self.tray.showMessage(
                "VoiceContext hotkey issue",
                detail[:500],
                QSystemTrayIcon.MessageIcon.Warning,
                7000,
            ),
        )

    def _hotkey_registration_changed(self, results: list) -> None:
        if results and all(result.success for result in results):
            self.main.statusBar().clearMessage()

    def _log_hotkey_action(self, action: str) -> None:
        state = self._session.state.value if self._session else SessionState.IDLE.value
        LOG.info("Global hotkey received: action=%s state=%s", action, state)

    def _fail_session(self, message: str) -> None:
        LOG.error("Session failed: %s", message)
        self.partial_timer.stop()
        self.elapsed_timer.stop()
        self.clipboard_watcher.stop()
        audio, self._audio = self._audio, None
        if audio is not None:
            self.executor.submit(audio.discard)
        engine, self._realtime_engine = self._realtime_engine, None
        self._realtime_closing = False
        self._resume_inflight = False
        if engine is not None:
            self.executor.submit(self._quiet_stop_realtime, engine)
        if self._session:
            self._session.error = message
            if self._session.asr_metadata.get("status") != "success":
                self._session.asr_metadata.update({"status": "failed", "error": message})
            self._session.output_metadata = {
                "clipboard_requested": False,
                "clipboard_verified": False,
                "attempts": 0,
                "status": "skipped",
            }
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
        self.hotkeys.close()
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


def run_profile_command(arguments: list[str]) -> int:
    """Handle --export-profile/--import-profile without launching the GUI."""
    usage = (
        "Usage:\n"
        "  VoiceContext --export-profile <path.zip>\n"
        "  VoiceContext --import-profile <path.zip-or-folder>"
    )

    def value_for(flag: str) -> str | None:
        try:
            return arguments[arguments.index(flag) + 1]
        except (ValueError, IndexError):
            return None

    from voice_context.storage.profile import (
        ProfileError,
        export_profile,
        import_profile,
    )

    if "--export-profile" in arguments:
        target = value_for("--export-profile")
        if not target:
            print(usage)
            return 2
        try:
            path = export_profile(Path(target).expanduser().resolve())
        except ProfileError as exc:
            print(f"Export failed: {exc}")
            return 1
        print(f"Profile exported to {path}")
        print("The archive contains settings, history, and API keys; store it safely.")
        return 0

    if "--import-profile" in arguments:
        source = value_for("--import-profile")
        if not source:
            print(usage)
            return 2
        try:
            summary = import_profile(Path(source).expanduser().resolve())
        except ProfileError as exc:
            print(f"Import failed: {exc}")
            return 1
        print(
            f"Profile imported: {summary['files_restored']} files and "
            f"{summary['keys_restored']} API keys restored."
        )
        print("Previous files were backed up inside %APPDATA%\\VoiceContext.")
        return 0

    print(usage)
    return 2


def main() -> int:
    if "--asr-self-test" in sys.argv:
        return run_asr_self_test(sys.argv[1:])
    if "--export-profile" in sys.argv or "--import-profile" in sys.argv:
        return run_profile_command(sys.argv[1:])
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shell32.SetCurrentProcessExplicitAppUserModelID("VoiceContext.Desktop")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("VoiceContext")
    app.setOrganizationName("VoiceContext")
    # Enable high-DPI scaling for crisp rendering on modern displays
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    config = ConfigStore()
    configure_logging(config)
    instance = SingleInstance.acquire()
    if instance is None:
        if SingleInstance.last_error:
            QMessageBox.critical(None, "VoiceContext could not start", SingleInstance.last_error)
            return 1
        return 0
    controller = VoiceContextController(app, instance)
    app._voicecontext_controller = controller  # type: ignore[attr-defined]
    return app.exec()
