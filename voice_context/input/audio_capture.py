from __future__ import annotations

import math
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from voice_context.storage.config_store import APP_DIR

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - exercised only without optional runtime dependency
    sd = None


class AudioCapture(QObject):
    level_changed = Signal(float)
    silence_detected = Signal()
    error = Signal(str)

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__()
        self.config = config
        self.sample_rate = int(config.get("sample_rate", 16000))
        self.channels = 1
        self._frames: list[bytes] = []
        self._lock = threading.RLock()
        self._stream = None
        self._started_at = 0.0
        self._silent_since: float | None = None
        self._silence_emitted = False
        self._chunk_consumer: Callable[[bytes], None] | None = None

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def duration_ms(self) -> int:
        with self._lock:
            samples = sum(len(frame) for frame in self._frames) // 2
        return int(samples / max(1, self.sample_rate) * 1000)

    def start(self) -> None:
        if self.is_recording:
            return
        if sd is None:
            raise RuntimeError("sounddevice is not installed. Run: pip install sounddevice")
        with self._lock:
            self._frames.clear()
        self._started_at = time.monotonic()
        self._silent_since = None
        self._silence_emitted = False
        device = self.config.get("input_device") or None
        block_ms = int(self.config.get("audio_chunk_ms", 100))
        try:
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                blocksize=max(256, int(self.sample_rate * block_ms / 1000)),
                device=device,
                channels=self.channels,
                dtype="int16",
                callback=self._callback,
            )
            self._stream.start()
        except Exception:
            self._stream = None
            raise

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.error.emit(str(status))
        raw = bytes(indata)
        with self._lock:
            self._frames.append(raw)
        if not raw:
            return
        if self._chunk_consumer is not None:
            try:
                self._chunk_consumer(raw)
            except Exception as exc:
                self.error.emit(f"Realtime audio delivery failed: {exc}")
        samples = memoryview(raw).cast("h")
        rms = math.sqrt(sum(sample * sample for sample in samples) / max(1, len(samples))) / 32768.0
        self.level_changed.emit(min(1.0, rms * 5))
        if not self.config.get("vad", True):
            return
        threshold = float(self.config.get("silence_threshold", 0.012))
        now = time.monotonic()
        if rms < threshold:
            self._silent_since = self._silent_since or now
            required = float(self.config.get("silence_ms", 900)) / 1000
            if now - self._silent_since >= required and not self._silence_emitted:
                self._silence_emitted = True
                self.silence_detected.emit()
        else:
            self._silent_since = None
            self._silence_emitted = False

    def stop(self, output_path: Path | None = None) -> Path:
        self.stop_stream()
        return self.snapshot(output_path)

    def stop_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def set_chunk_consumer(self, consumer: Callable[[bytes], None] | None) -> None:
        self._chunk_consumer = consumer

    def snapshot(self, output_path: Path | None = None) -> Path:
        cache = APP_DIR / "audio_cache"
        cache.mkdir(parents=True, exist_ok=True)
        path = output_path or cache / f"capture-{time.time_ns()}.wav"
        with self._lock:
            frames = list(self._frames)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(b"".join(frames))
        return path

    def discard(self) -> None:
        if self.is_recording:
            try:
                self.stop_stream()
            except Exception:
                pass
        self._chunk_consumer = None
        with self._lock:
            self._frames.clear()

    @staticmethod
    def devices() -> list[dict[str, Any]]:
        if sd is None:
            return []
        try:
            return [dict(item) for item in sd.query_devices() if item.get("max_input_channels", 0) > 0]
        except Exception:
            return []
