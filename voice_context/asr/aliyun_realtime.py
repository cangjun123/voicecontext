from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Signal

from voice_context.core.events import join_speech_segments

from .base import ASRError, TranscriptionResult


def load_wav_as_pcm(audio_path: Path, target_rate: int) -> tuple[bytes, float]:
    """Load a PCM WAV for the remote realtime endpoint without ML/audio libraries."""
    try:
        with wave.open(str(audio_path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            source_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
    except (OSError, wave.Error) as exc:
        raise ASRError(f"ASR test audio must be an uncompressed PCM WAV file: {exc}") from exc

    if not frames:
        return b"", 0.0
    if channels == 1 and sample_width == 2 and source_rate == target_rate:
        return frames, len(frames) / (target_rate * 2)
    if channels not in {1, 2} or sample_width not in {1, 2, 3, 4}:
        raise ASRError("ASR test WAV must use mono/stereo PCM with 8, 16, 24, or 32-bit samples.")

    try:
        import audioop
    except ImportError as exc:  # pragma: no cover - Python 3.13+ fallback path
        raise ASRError(
            f"ASR test WAV must be mono, 16-bit, and {target_rate} Hz on this Python version."
        ) from exc
    if sample_width == 1:
        frames = audioop.bias(frames, 1, -128)
    if channels == 2:
        frames = audioop.tomono(frames, sample_width, 0.5, 0.5)
    if sample_width != 2:
        frames = audioop.lin2lin(frames, sample_width, 2)
    if source_rate != target_rate:
        frames = audioop.ratecv(frames, 2, 1, source_rate, target_rate, None)[0]
    return frames, len(frames) / (target_rate * 2)


def normalize_websocket_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlsplit(raw)
    if not parsed.hostname:
        raise ASRError("Aliyun Model Studio URL does not contain a valid host name.")
    port = f":{parsed.port}" if parsed.port else ""
    host = f"{parsed.hostname}{port}"
    path = parsed.path.rstrip("/")
    if path.endswith("/api-ws/v1/inference"):
        return f"wss://{host}{path}"
    return f"wss://{host}/api-ws/v1/inference"


@dataclass(slots=True)
class RealtimeRecognitionResult:
    text: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    request_id: str = ""
    first_package_delay_ms: float = -1
    last_package_delay_ms: float = -1


class _DashScopeCallback:
    def __init__(self, owner: AliyunRealtimeASR) -> None:
        self.owner = owner

    def on_open(self) -> None:
        self.owner.connection_opened.emit()

    def on_event(self, result) -> None:
        self.owner._handle_event(result)

    def on_complete(self) -> None:
        self.owner._complete_event.set()

    def on_error(self, result) -> None:
        code = getattr(result, "code", "") or "DashScopeError"
        message = getattr(result, "message", "") or str(result)
        self.owner._set_error(f"{code}: {message}")

    def on_close(self) -> None:
        self.owner._closed_event.set()


class AliyunRealtimeASR(QObject):
    """True streaming ASR backed by Alibaba Cloud Model Studio WebSocket API."""

    transcript_updated = Signal(str)
    sentence_final = Signal(str, int, int)
    connection_opened = Signal()
    error = Signal(str)

    def __init__(self, config: dict[str, Any], api_key: str) -> None:
        super().__init__()
        self.config = config
        self.api_key = api_key.strip()
        self._recognition = None
        self._lock = threading.RLock()
        self._final_segments: list[dict[str, Any]] = []
        self._partial = ""
        self._error = ""
        self._running = False
        self._complete_event = threading.Event()
        self._closed_event = threading.Event()

    @property
    def websocket_url(self) -> str:
        return normalize_websocket_url(str(self.config.get("base_url", "")))

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if not self.api_key:
            raise ASRError("Aliyun Model Studio API key is empty. Configure it in Settings > ASR.")
        if self._running:
            raise ASRError("Aliyun realtime recognition is already running.")
        try:
            import dashscope
            from dashscope.audio.asr import Recognition
        except ImportError as exc:
            raise ASRError("DashScope SDK is not installed. Run: pip install dashscope") from exc

        dashscope.base_websocket_api_url = self.websocket_url
        callback = _DashScopeCallback(self)
        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "semantic_punctuation_enabled": bool(
                self.config.get("semantic_punctuation", False)
            ),
            "disfluency_removal_enabled": bool(
                self.config.get("disfluency_removal", False)
            ),
        }
        headers = self.config.get("custom_headers")
        if isinstance(headers, dict) and headers:
            kwargs["headers"] = headers
        self._recognition = Recognition(
            model=str(self.config.get("model") or "fun-asr-realtime"),
            callback=callback,
            format="pcm",
            sample_rate=int(self.config.get("sample_rate", 16000)),
            **kwargs,
        )
        self._complete_event.clear()
        self._closed_event.clear()
        self._error = ""
        self._final_segments.clear()
        self._partial = ""
        try:
            self._recognition.start(phrase_id=str(self.config.get("phrase_id", "")) or None)
        except Exception as exc:
            self._recognition = None
            raise ASRError(f"Could not start Aliyun realtime ASR: {exc}") from exc
        self._running = True

    def send_audio(self, pcm: bytes) -> None:
        if not pcm:
            return
        recognition = self._recognition
        if not self._running or recognition is None:
            return
        try:
            recognition.send_audio_frame(pcm)
        except Exception as exc:
            self._set_error(f"Aliyun realtime audio send failed: {exc}")

    def _handle_event(self, result) -> None:
        sentence = result.get_sentence() or {}
        if not isinstance(sentence, dict):
            return
        text = str(sentence.get("text", "")).strip()
        end_time = sentence.get("end_time")
        begin_time = int(sentence.get("begin_time") or 0)
        with self._lock:
            if end_time is not None:
                if text:
                    segment = {
                        "text": text,
                        "start_ms": begin_time,
                        "end_ms": int(end_time),
                    }
                    self._final_segments.append(segment)
                    self.sentence_final.emit(text, begin_time, int(end_time))
                self._partial = ""
            else:
                self._partial = text
            full_text = join_speech_segments(
                [segment["text"] for segment in self._final_segments]
                + ([self._partial] if self._partial else [])
            )
        self.transcript_updated.emit(full_text)

    def _set_error(self, message: str) -> None:
        with self._lock:
            if self._error:
                return
            self._error = message
        self.error.emit(message)
        self._complete_event.set()

    def stop(self) -> RealtimeRecognitionResult:
        recognition = self._recognition
        if recognition is not None and self._running:
            try:
                recognition.stop()
            except Exception as exc:
                # The SDK marks the stream stopped before invoking callbacks on server errors.
                if not self._error:
                    self._set_error(f"Could not stop Aliyun realtime ASR: {exc}")
        self._running = False
        self._recognition = None
        with self._lock:
            error = self._error
            segments = [dict(segment) for segment in self._final_segments]
            if self._partial:
                last_end = segments[-1]["end_ms"] if segments else 0
                segments.append(
                    {"text": self._partial, "start_ms": last_end, "end_ms": last_end}
                )
            text = join_speech_segments(segment["text"] for segment in segments)
        if error:
            raise ASRError(error)
        return RealtimeRecognitionResult(
            text=text,
            segments=segments,
            request_id=str(recognition.get_last_request_id()) if recognition else "",
            first_package_delay_ms=float(recognition.get_first_package_delay()) if recognition else -1,
            last_package_delay_ms=float(recognition.get_last_package_delay()) if recognition else -1,
        )

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        """Exercise the same realtime endpoint with an audio file for Settings diagnostics."""
        sample_rate = int(self.config.get("sample_rate", 16000))
        pcm, duration_s = load_wav_as_pcm(audio_path, sample_rate)
        self.start()
        chunk_bytes = max(640, sample_rate // 10 * 2)
        try:
            for offset in range(0, len(pcm), chunk_bytes):
                self.send_audio(pcm[offset : offset + chunk_bytes])
                time.sleep(0.1)
            result = self.stop()
        except Exception:
            if self.running:
                try:
                    self.stop()
                except Exception:
                    pass
            raise
        return TranscriptionResult(
            text=result.text,
            language=str(self.config.get("language", "")),
            duration_s=duration_s,
            segments=[
                {
                    "text": segment["text"],
                    "start": segment["start_ms"] / 1000,
                    "end": segment["end_ms"] / 1000,
                }
                for segment in result.segments
            ],
        )
