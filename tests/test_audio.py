from __future__ import annotations

import wave
from array import array
from types import SimpleNamespace

import voice_context.input.audio_capture as audio_module
from voice_context.input.audio_capture import AudioCapture


def test_audio_snapshot_writes_pcm_wave(tmp_path) -> None:
    capture = AudioCapture({"sample_rate": 16000})
    capture._frames = [b"\x01\x00" * 1600]
    path = capture.snapshot(tmp_path / "capture.wav")
    with wave.open(str(path), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() == 1600
    assert capture.duration_ms == 100


def test_audio_chunks_can_be_forwarded_to_realtime_consumer() -> None:
    capture = AudioCapture({"sample_rate": 16000, "vad": False})
    observed = []
    capture.set_chunk_consumer(observed.append)
    raw = b"\x01\x00" * 1600
    capture._callback(raw, 1600, None, None)
    assert observed == [raw]


def test_device_ids_distinguish_host_apis_and_survive_index_changes(monkeypatch) -> None:
    devices = [
        {"name": "Microphone", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 44100},
        {"name": "Microphone", "hostapi": 1, "max_input_channels": 1, "default_samplerate": 48000},
    ]

    class FakeSoundDevice:
        default = SimpleNamespace(device=(0, 0))

        def query_hostapis(self):
            return [{"name": "MME"}, {"name": "Windows WASAPI"}]

        def query_devices(self):
            return devices

    monkeypatch.setattr(audio_module, "sd", FakeSoundDevice())
    choices = AudioCapture.devices()
    assert choices[0]["id"] != choices[1]["id"]
    assert AudioCapture.resolve_input_device(choices[1]["id"]) == 1
    assert AudioCapture.resolve_input_device("Microphone") == 0

    devices.insert(0, {"name": "Speaker", "hostapi": 0, "max_input_channels": 0})
    assert AudioCapture.resolve_input_device(choices[1]["id"]) == 2


def test_native_rate_fallback_streams_16khz_pcm(monkeypatch) -> None:
    attempted_rates = []

    class FakeStream:
        def __init__(self, **kwargs):
            attempted_rates.append(kwargs["samplerate"])
            if kwargs["samplerate"] == 16000:
                raise RuntimeError("Invalid sample rate")

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    class FakeSoundDevice:
        default = SimpleNamespace(device=(0, 0))
        RawInputStream = FakeStream

        def query_hostapis(self):
            return [{"name": "Windows WASAPI"}]

        def query_devices(self, device=None, kind=None):
            items = [{"name": "Microphone", "hostapi": 0, "max_input_channels": 1, "default_samplerate": 48000}]
            return items if device is None and kind is None else items[0]

    monkeypatch.setattr(audio_module, "sd", FakeSoundDevice())
    capture = AudioCapture({"input_device": "", "sample_rate": 16000, "vad": False})
    observed = []
    capture.set_chunk_consumer(observed.append)
    capture.start()
    assert attempted_rates == [16000, 48000]

    source = array("h", range(4800))
    capture._callback(source[:2400].tobytes(), 2400, None, None)
    capture._callback(source[2400:].tobytes(), 2400, None, None)
    result = array("h")
    result.frombytes(b"".join(observed))
    assert result.tolist() == list(range(0, 4800, 3))
    assert capture.duration_ms == 100
    capture.stop_stream()


def test_audio_prebuffer_flushes_in_order_without_dropping_concurrent_chunks() -> None:
    capture = AudioCapture({"sample_rate": 16000, "vad": False})
    chunk1 = b"\x01\x00" * 800
    chunk2 = b"\x02\x00" * 800
    chunk3 = b"\x03\x00" * 800
    chunk4 = b"\x04\x00" * 800

    # Recorded before remote WebSocket finishes connecting
    capture._callback(chunk1, 800, None, None)
    capture._callback(chunk2, 800, None, None)

    delivered: list[bytes] = []

    def slow_consumer(data: bytes) -> None:
        delivered.append(data)
        # Simulate PortAudio delivering chunk3 mid-flush while chunk1 is being sent
        if data == chunk1:
            capture._callback(chunk3, 800, None, None)

    capture.flush_and_set_chunk_consumer(slow_consumer)
    # Subsequent live chunk goes straight to consumer
    capture._callback(chunk4, 800, None, None)

    assert delivered == [chunk1, chunk2, chunk3, chunk4]
