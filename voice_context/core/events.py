from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ContentType = Literal["file", "code", "error", "log", "url", "text"]


@dataclass(slots=True)
class SpeechEvent:
    text: str
    start_ms: int
    end_ms: int
    is_final: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "speech",
            "text": self.text,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "isFinal": self.is_final,
        }


@dataclass(slots=True)
class ReferenceEvent:
    id: str
    captured_at_ms: int
    content: str
    content_type: ContentType = "text"
    preview: str = ""
    name: str = ""
    source_process: str | None = None
    source_window: str | None = None
    transcript_at_capture: str = ""

    def __post_init__(self) -> None:
        if not self.preview:
            self.preview = " ".join(self.content.strip().split())[:120]
        if not self.name:
            self.name = self._default_name()

    def _default_name(self) -> str:
        if self.content_type == "file":
            return self.content.strip().splitlines()[0].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        labels = {"code": "selected code", "error": "error", "log": "log", "url": "URL"}
        return labels.get(self.content_type, self.preview[:36] or self.id)

    def to_dict(self, include_content: bool = True) -> dict[str, Any]:
        data = {
            "type": "reference",
            "id": self.id,
            "capturedAtMs": self.captured_at_ms,
            "contentType": self.content_type,
            "preview": self.preview,
            "name": self.name,
            "sourceProcess": self.source_process,
            "sourceWindow": self.source_window,
            "transcriptAtCapture": self.transcript_at_capture,
        }
        if include_content:
            data["content"] = self.content
        return data


@dataclass(slots=True)
class SessionEvent:
    type: str
    timestamp_ms: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestampMs": self.timestamp_ms,
            "payload": dict(self.payload),
        }


TimelineEvent = SpeechEvent | ReferenceEvent | SessionEvent


def event_from_dict(data: dict[str, Any]) -> TimelineEvent:
    event_type = data.get("type")
    if event_type == "speech":
        return SpeechEvent(
            text=data.get("text", ""),
            start_ms=int(data.get("startMs", data.get("start_ms", 0))),
            end_ms=int(data.get("endMs", data.get("end_ms", 0))),
            is_final=bool(data.get("isFinal", True)),
        )
    if event_type == "reference":
        return ReferenceEvent(
            id=data.get("id", "ref_1"),
            captured_at_ms=int(data.get("capturedAtMs", data.get("captured_at_ms", 0))),
            content=data.get("content", ""),
            content_type=data.get("contentType", "text"),
            preview=data.get("preview", ""),
            name=data.get("name", ""),
            source_process=data.get("sourceProcess"),
            source_window=data.get("sourceWindow"),
            transcript_at_capture=data.get("transcriptAtCapture", ""),
        )
    return SessionEvent(
        type=event_type or "unknown",
        timestamp_ms=int(data.get("timestamp_ms", data.get("timestampMs", 0))),
        payload=dict(data.get("payload", {})),
    )
