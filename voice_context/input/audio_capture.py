from __future__ import annotations

import array
import json
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
        self._capture_rate = self.sample_rate
        self._previous_sample: int | None = None
        self._input_sample_index = 0
        self._next_output_position = 0
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
        device = self.resolve_input_device(self.config.get("input_device"))
        self._previous_sample = None
        self._input_sample_index = 0
        self._next_output_position = 0
        block_ms = int(self.config.get("audio_chunk_ms", 100))
        native_rate = int(sd.query_devices(device, "input")["default_samplerate"])
        try:
            self._start_stream(device, self.sample_rate, block_ms)
        except Exception as original_error:
            if native_rate == self.sample_rate:
                raise
            try:
                self._start_stream(device, native_rate, block_ms)
            except Exception as native_error:
                raise RuntimeError(
                    f"Could not open input at {self.sample_rate} Hz ({original_error}) "
                    f"or its native {native_rate} Hz ({native_error})"
                ) from native_error

    def _start_stream(self, device: int | None, rate: int, block_ms: int) -> None:
        self._capture_rate = rate
        stream = sd.RawInputStream(
            samplerate=rate,
            blocksize=max(256, int(rate * block_ms / 1000)),
            device=device,
            channels=self.channels,
            dtype="int16",
            callback=self._callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self._stream = stream

    def _resample(self, raw: bytes) -> bytes:
        if self._capture_rate == self.sample_rate:
            return raw
        samples = array.array("h")
        samples.frombytes(raw)
        output = array.array("h")
        target_rate = self.sample_rate
        source_rate = self._capture_rate
        for current in samples:
            previous = self._previous_sample
            if previous is None:
                output.append(current)
                self._next_output_position = source_rate
            else:
                end = self._input_sample_index * target_rate
                start = end - target_rate
                while self._next_output_position <= end:
                    fraction = self._next_output_position - start
                    interpolated = previous + (current - previous) * fraction / target_rate
                    output.append(max(-32768, min(32767, round(interpolated))))
                    self._next_output_position += source_rate
            self._previous_sample = current
            self._input_sample_index += 1
        return output.tobytes()

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.error.emit(str(status))
        raw = self._resample(bytes(indata))
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
        if output_path is None:
            cache = APP_DIR / "audio_cache"
            cache.mkdir(parents=True, exist_ok=True)
            path = cache / f"capture-{time.time_ns()}.wav"
        else:
            path = output_path
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
            hostapis = sd.query_hostapis()
            devices = []
            for index, item in enumerate(sd.query_devices()):
                if item.get("max_input_channels", 0) <= 0:
                    continue
                entry = dict(item)
                entry["index"] = index
                entry["hostapi_name"] = hostapis[item["hostapi"]]["name"]
                entry["id"] = "pa:" + json.dumps(
                    [entry["hostapi_name"], entry["name"], index],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                devices.append(entry)
            return devices
        except Exception:
            return []

    @staticmethod
    def resolve_input_device(value: Any) -> int | None:
        if not value:
            return None
        devices = AudioCapture.devices()
        if str(value).startswith("pa:"):
            try:
                hostapi, name, old_index = json.loads(str(value)[3:])
            except (ValueError, TypeError) as exc:
                raise ValueError("The saved input device ID is invalid.") from exc
            matches = [
                item for item in devices
                if item["hostapi_name"] == hostapi and item["name"] == name
            ]
            for item in matches:
                if item["index"] == old_index:
                    return item["index"]
            if len(matches) == 1:
                return matches[0]["index"]
        else:
            matches = [item for item in devices if item["name"] == str(value)]
            if matches:
                default_index = sd.default.device[0]
                for item in matches:
                    if item["index"] == default_index:
                        return default_index
                return matches[0]["index"]
        raise ValueError(f"Input device {value!r} was not found. Choose an available device in Settings > ASR.")
