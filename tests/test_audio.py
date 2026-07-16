from __future__ import annotations

import wave

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
