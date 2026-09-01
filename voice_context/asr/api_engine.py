from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from voice_context.core.tls import SSL_CONTEXT

from .base import ASREngine, ASRError, TranscriptionResult


class APIASREngine(ASREngine):
    def __init__(self, config: dict[str, Any], api_key: str = "", custom: bool = False) -> None:
        self.config = config
        self.api_key = api_key
        self.custom = custom

    def _url(self) -> str:
        base = str(self.config.get("base_url", "")).strip().rstrip("/")
        if not base:
            raise ASRError("ASR API URL is empty. Configure it in Settings > ASR.")
        if self.custom and any(base.lower().endswith(suffix) for suffix in ("/transcribe", "/transcription")):
            return base
        if base.lower().endswith("/audio/transcriptions"):
            return base
        if not self.custom and urlsplit(base).path in {"", "/"}:
            base += "/v1"
        return f"{base}/audio/transcriptions"

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        if not audio_path.exists() or audio_path.stat().st_size <= 44:
            return TranscriptionResult("")
        headers = {str(k): str(v) for k, v in self.config.get("custom_headers", {}).items()}
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        data = {
            "model": str(self.config.get("model", "whisper-1")),
            "language": str(self.config.get("language", "")),
            "response_format": "json",
        }
        data = {key: value for key, value in data.items() if value}
        timeout = float(self.config.get("timeout_s", 60))
        try:
            with audio_path.open("rb") as stream:
                response = httpx.post(
                    self._url(),
                    headers=headers,
                    data=data,
                    files={"file": (audio_path.name, stream, "audio/wav")},
                    timeout=timeout,
                    verify=SSL_CONTEXT,
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400]
            raise ASRError(f"ASR API returned HTTP {exc.response.status_code}: {detail}") from exc
        except (httpx.HTTPError, OSError) as exc:
            raise ASRError(f"Cannot reach ASR service: {exc}") from exc

        try:
            payload = response.json()
        except json.JSONDecodeError:
            text = response.text.strip()
            if text:
                return TranscriptionResult(text)
            raise ASRError("ASR service returned an empty non-JSON response.")
        text = self._extract_text(payload)
        if text is None:
            raise ASRError("ASR response does not contain a recognized text field.")
        return TranscriptionResult(
            text=text.strip(),
            language=str(payload.get("language", "")) if isinstance(payload, dict) else "",
            duration_s=float(payload.get("duration", 0) or 0) if isinstance(payload, dict) else 0,
            segments=payload.get("segments", []) if isinstance(payload, dict) else [],
        )

    @staticmethod
    def _extract_text(payload: Any) -> str | None:
        if isinstance(payload, str):
            return payload
        if not isinstance(payload, dict):
            return None
        for key in ("text", "transcript", "result", "content"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                nested = APIASREngine._extract_text(value)
                if nested is not None:
                    return nested
        data = payload.get("data")
        return APIASREngine._extract_text(data) if data is not None else None
