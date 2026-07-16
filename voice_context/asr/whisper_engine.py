from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ASREngine, ASRError, TranscriptionResult


class WhisperEngine(ASREngine):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ASRError('faster-whisper is not installed. Run: pip install -e ".[whisper]"') from exc
        model = self.config.get("model_path") or self.config.get("model") or "small"
        device = str(self.config.get("device", "cpu"))
        compute_type = "float16" if device == "cuda" else "int8"
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute_type)
        except Exception as exc:
            raise ASRError(f"Could not load faster-whisper model {model!r}: {exc}") from exc
        return self._model

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        model = self._load()
        try:
            segments, info = model.transcribe(
                str(audio_path),
                language=self.config.get("language") or None,
                vad_filter=bool(self.config.get("vad", True)),
                hotwords=self.config.get("hotwords") or None,
                beam_size=5,
            )
            items = list(segments)
        except Exception as exc:
            raise ASRError(f"faster-whisper transcription failed: {exc}") from exc
        result_segments = [
            {"start": item.start, "end": item.end, "text": item.text.strip()} for item in items
        ]
        return TranscriptionResult(
            text="".join(item.text for item in items).strip(),
            language=getattr(info, "language", ""),
            duration_s=float(getattr(info, "duration", 0) or 0),
            segments=result_segments,
        )
