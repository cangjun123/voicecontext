from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ASREngine, ASRError, TranscriptionResult


class FunASREngine(ASREngine):
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self._model = None
        self._vad_model = None
        self._punc_model = None
        self._backend = ""

    def _resolve_onnx_paths(self) -> tuple[Path | None, Path | None, Path | None]:
        value = str(self.config.get("model_path", "")).strip()
        if not value:
            return None, None, None
        configured = Path(value).expanduser()
        if not configured.is_dir():
            return None, None, None

        if (configured / "model_quant.onnx").exists() and "paraformer" in configured.name.lower():
            asr_path = configured
            root = configured.parent
        else:
            root = configured
            language = str(self.config.get("language", "zh")).lower()
            candidates = [
                path
                for path in root.iterdir()
                if path.is_dir()
                and "paraformer" in path.name.lower()
                and "asr" in path.name.lower()
                and (path / "model_quant.onnx").exists()
            ]
            if language.startswith("en"):
                localized = [path for path in candidates if "-en-" in path.name.lower()]
            else:
                localized = [path for path in candidates if "-zh-" in path.name.lower()]
            candidates = localized or candidates
            # The non-contextual export is directly supported by funasr-onnx. Contextual exports
            # require matching quantized embedding weights, which older ModelScope caches may omit.
            candidates.sort(key=lambda path: ("contextual" in path.name.lower(), path.name.lower()))
            asr_path = candidates[0] if candidates else None

        siblings = list(root.iterdir()) if root.is_dir() else []
        vad_path = next(
            (
                path
                for path in siblings
                if path.is_dir()
                and "fsmn_vad" in path.name.lower()
                and (path / "model_quant.onnx").exists()
            ),
            None,
        )
        punc_path = next(
            (
                path
                for path in siblings
                if path.is_dir()
                and path.name.lower().startswith("punc_")
                and (path / "model_quant.onnx").exists()
            ),
            None,
        )
        return asr_path, vad_path, punc_path

    def _load_onnx(self, asr_path: Path, vad_path: Path | None, punc_path: Path | None):
        try:
            from funasr_onnx import CT_Transformer, Fsmn_vad, Paraformer
        except ImportError as exc:
            raise ASRError(
                'FunASR ONNX runtime is not installed. Run: pip install -e ".[funasr]"'
            ) from exc
        device_id = "0" if str(self.config.get("device", "cpu")) == "cuda" else "-1"
        try:
            self._model = Paraformer(
                asr_path,
                batch_size=1,
                device_id=device_id,
                quantize=(asr_path / "model_quant.onnx").exists(),
            )
            if self.config.get("vad", True) and vad_path:
                self._vad_model = Fsmn_vad(
                    vad_path,
                    batch_size=1,
                    device_id=device_id,
                    quantize=(vad_path / "model_quant.onnx").exists(),
                    max_end_sil=int(self.config.get("silence_ms", 900)),
                )
            if self.config.get("punctuation", True) and punc_path:
                self._punc_model = CT_Transformer(
                    punc_path,
                    batch_size=1,
                    device_id=device_id,
                    quantize=(punc_path / "model_quant.onnx").exists(),
                )
        except Exception as exc:
            raise ASRError(f"Could not load local FunASR ONNX model at {asr_path}: {exc}") from exc
        self._backend = "onnx"
        return self._model

    def _load(self):
        if self._model is not None:
            return self._model
        asr_path, vad_path, punc_path = self._resolve_onnx_paths()
        if asr_path:
            return self._load_onnx(asr_path, vad_path, punc_path)
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise ASRError(
                'No compatible FunASR runtime is installed. Run: pip install -e ".[funasr]"'
            ) from exc
        kwargs: dict[str, Any] = {
            "model": self.config.get("model_path") or self.config.get("model") or "paraformer-zh",
            "device": self.config.get("device", "cpu"),
            "disable_update": True,
        }
        if self.config.get("vad"):
            kwargs["vad_model"] = "fsmn-vad"
        if self.config.get("punctuation"):
            kwargs["punc_model"] = "ct-punc"
        try:
            self._model = AutoModel(**kwargs)
        except Exception as exc:
            raise ASRError(f"Could not load FunASR model: {exc}") from exc
        self._backend = "native"
        return self._model

    @staticmethod
    def _onnx_text(result: Any) -> str:
        if not isinstance(result, list):
            return ""
        chunks: list[str] = []
        for item in result:
            if not isinstance(item, dict):
                continue
            prediction = item.get("preds", "")
            if isinstance(prediction, (tuple, list)) and prediction:
                prediction = prediction[0]
            if isinstance(prediction, str):
                chunks.append(prediction.strip())
        return "".join(chunks)

    def _transcribe_onnx(self, audio_path: Path) -> TranscriptionResult:
        inputs: list[str | Any] = [str(audio_path)]
        segment_ranges: list[tuple[int, int]] = []
        duration_s = 0.0
        if self._vad_model is not None:
            import librosa

            waveform, _ = librosa.load(str(audio_path), sr=16000, mono=True)
            duration_s = len(waveform) / 16000
            vad_result = self._vad_model(str(audio_path))
            raw_ranges = vad_result[0] if isinstance(vad_result, list) and vad_result else []
            inputs = []
            for value in raw_ranges:
                if not isinstance(value, (list, tuple)) or len(value) < 2:
                    continue
                start_ms, end_ms = int(value[0]), int(value[1])
                if end_ms < 0:
                    end_ms = int(duration_s * 1000)
                if end_ms <= start_ms:
                    continue
                start_sample = max(0, start_ms * 16)
                end_sample = min(len(waveform), end_ms * 16)
                if end_sample - start_sample < 1600:
                    continue
                inputs.append(waveform[start_sample:end_sample])
                segment_ranges.append((start_ms, end_ms))
            # An unfinished VAD range is common during partial transcription. Fall back to the
            # complete snapshot so streaming text does not stall mid-utterance.
            if not inputs and len(waveform) >= 1600:
                inputs = [waveform]
                segment_ranges = [(0, int(duration_s * 1000))]

        texts: list[str] = []
        segments: list[dict[str, Any]] = []
        for index, audio in enumerate(inputs):
            result = self._model(audio)
            text = self._onnx_text(result)
            if not text:
                continue
            texts.append(text)
            if index < len(segment_ranges):
                start_ms, end_ms = segment_ranges[index]
                segments.append({"start": start_ms / 1000, "end": end_ms / 1000, "text": text})
        text = "".join(texts).strip()
        if text and self._punc_model is not None:
            punctuated = self._punc_model(text)
            if isinstance(punctuated, (tuple, list)) and punctuated:
                punctuated = punctuated[0]
            if isinstance(punctuated, str):
                text = punctuated.strip()
        return TranscriptionResult(text=text, language=str(self.config.get("language", "")), duration_s=duration_s, segments=segments)

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        try:
            model = self._load()
            if self._backend == "onnx":
                return self._transcribe_onnx(audio_path)
            result = model.generate(
                input=str(audio_path),
                cache={},
                language=self.config.get("language", "zh"),
                hotword=self.config.get("hotwords", ""),
                batch_size_s=60,
            )
        except ASRError:
            raise
        except Exception as exc:
            raise ASRError(f"FunASR transcription failed: {exc}") from exc
        if not result:
            return TranscriptionResult("")
        text = "".join(str(item.get("text", "")) for item in result if isinstance(item, dict)).strip()
        return TranscriptionResult(text=text, segments=list(result))
