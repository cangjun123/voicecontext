from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .events import ContentType, ReferenceEvent, SessionEvent, SpeechEvent, TimelineEvent


class SessionState(str, Enum):
    IDLE = "Idle"
    ARMED = "Armed"
    LISTENING = "Listening"
    PAUSED = "Paused"
    COMPOSING = "Composing"
    PROMPT_READY = "PromptReady"
    ERROR = "Error"


ALLOWED_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.ARMED, SessionState.LISTENING, SessionState.ERROR},
    SessionState.ARMED: {SessionState.LISTENING, SessionState.IDLE, SessionState.ERROR},
    SessionState.LISTENING: {
        SessionState.PAUSED,
        SessionState.COMPOSING,
        SessionState.IDLE,
        SessionState.ERROR,
    },
    SessionState.PAUSED: {
        SessionState.LISTENING,
        SessionState.COMPOSING,
        SessionState.IDLE,
        SessionState.ERROR,
    },
    SessionState.COMPOSING: {SessionState.PROMPT_READY, SessionState.ERROR},
    SessionState.PROMPT_READY: {SessionState.IDLE, SessionState.COMPOSING, SessionState.ERROR},
    SessionState.ERROR: {SessionState.IDLE},
}


def now_ms() -> int:
    return int(time.time() * 1000)


def classify_content(content: str, hinted_type: str | None = None) -> ContentType:
    if hinted_type in {"file", "code", "error", "log", "url", "text"}:
        return hinted_type  # type: ignore[return-value]
    value = content.strip()
    if re.fullmatch(r"https?://\S+", value, re.IGNORECASE):
        return "url"
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if lines and all(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/).+", line) for line in lines):
        return "file"
    lowered = value.lower()
    if any(token in lowered for token in ("traceback", "exception:", "error:", "failed", "errno")):
        return "error"
    if len(lines) >= 2 and any(re.match(r"^\d{4}-\d{2}-\d{2}[ t]", line) for line in lines[:3]):
        return "log"
    code_markers = ("def ", "class ", "function ", "const ", "import ", "#include", "=>", "{", "};")
    if len(lines) >= 2 and any(marker in value for marker in code_markers):
        return "code"
    return "text"


@dataclass
class VoiceSession:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: SessionState = SessionState.IDLE
    created_at_ms: int = field(default_factory=now_ms)
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    events: list[TimelineEvent] = field(default_factory=list)
    partial_transcript: str = ""
    final_prompt: str = ""
    composer_metadata: dict = field(default_factory=dict)
    error: str = ""
    source_app: str = ""
    _ignored_content: str = field(default="", repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def transition(self, target: SessionState) -> None:
        with self._lock:
            if target == self.state:
                return
            if target not in ALLOWED_TRANSITIONS[self.state]:
                raise ValueError(f"Invalid session transition: {self.state.value} -> {target.value}")
            self.state = target
            self.events.append(SessionEvent("state", self.elapsed_ms(), {"state": target.value}))
            if target == SessionState.LISTENING and self.started_at_ms is None:
                self.started_at_ms = now_ms()
            if target in {SessionState.PROMPT_READY, SessionState.ERROR}:
                self.ended_at_ms = now_ms()

    def elapsed_ms(self) -> int:
        anchor = self.started_at_ms or self.created_at_ms
        return max(0, now_ms() - anchor)

    @property
    def speech_events(self) -> list[SpeechEvent]:
        return [event for event in self.events if isinstance(event, SpeechEvent)]

    @property
    def references(self) -> list[ReferenceEvent]:
        return [event for event in self.events if isinstance(event, ReferenceEvent)]

    @property
    def transcript(self) -> str:
        final = " ".join(event.text.strip() for event in self.speech_events if event.is_final and event.text.strip())
        if self.partial_transcript.strip():
            return " ".join(part for part in (final, self.partial_transcript.strip()) if part)
        return final

    def set_partial(self, text: str) -> None:
        with self._lock:
            self.partial_transcript = text.strip()

    def add_speech(self, text: str, start_ms: int, end_ms: int, is_final: bool = True) -> SpeechEvent | None:
        value = text.strip()
        if not value:
            return None
        with self._lock:
            if is_final:
                self.partial_transcript = ""
            event = SpeechEvent(value, start_ms, end_ms, is_final)
            self.events.append(event)
            return event

    def add_reference(
        self,
        content: str,
        source_process: str | None = None,
        source_window: str | None = None,
        content_type: str | None = None,
        captured_at_ms: int | None = None,
        dedupe: bool = True,
        transcript_at_capture: str | None = None,
    ) -> ReferenceEvent | None:
        value = content.replace("\x00", "").strip()
        if not value:
            return None
        with self._lock:
            if value == self._ignored_content:
                self._ignored_content = ""
                return None
            if dedupe and any(ref.content == value for ref in self.references):
                return None
            event = ReferenceEvent(
                id=f"ref_{len(self.references) + 1}",
                captured_at_ms=captured_at_ms if captured_at_ms is not None else self.elapsed_ms(),
                content=value,
                content_type=classify_content(value, content_type),
                source_process=source_process,
                source_window=source_window,
                transcript_at_capture=(
                    self.transcript if transcript_at_capture is None else transcript_at_capture.strip()
                ),
            )
            self.events.append(event)
            return event

    def remove_reference(self, reference_id: str) -> ReferenceEvent | None:
        with self._lock:
            for index, event in enumerate(self.events):
                if isinstance(event, ReferenceEvent) and event.id == reference_id:
                    removed = self.events.pop(index)
                    self._renumber_references()
                    return removed
        return None

    def undo_reference(self) -> ReferenceEvent | None:
        refs = self.references
        return self.remove_reference(refs[-1].id) if refs else None

    def ignore_last_capture(self) -> ReferenceEvent | None:
        removed = self.undo_reference()
        if removed:
            self._ignored_content = removed.content
        return removed

    def update_reference(self, reference_id: str, name: str, content_type: str) -> bool:
        for ref in self.references:
            if ref.id == reference_id:
                ref.name = name.strip() or ref.id
                ref.content_type = classify_content(ref.content, content_type)
                return True
        return False

    def _renumber_references(self) -> None:
        for index, ref in enumerate(self.references, start=1):
            ref.id = f"ref_{index}"

    def sorted_content_events(self) -> list[SpeechEvent | ReferenceEvent]:
        content_events: Iterable[SpeechEvent | ReferenceEvent] = (
            event for event in self.events if isinstance(event, (SpeechEvent, ReferenceEvent))
        )
        return sorted(
            content_events,
            key=lambda item: item.start_ms if isinstance(item, SpeechEvent) else item.captured_at_ms,
        )

    def to_dict(self, include_reference_content: bool = True) -> dict:
        def serialize(event: TimelineEvent) -> dict:
            if isinstance(event, ReferenceEvent):
                return event.to_dict(include_reference_content)
            return event.to_dict()

        stored_prompt = self.final_prompt
        if not include_reference_content:
            for reference in self.references:
                stored_prompt = stored_prompt.replace(reference.content, "[reference content not saved]")
        return {
            "id": self.id,
            "state": self.state.value,
            "createdAtMs": self.created_at_ms,
            "startedAtMs": self.started_at_ms,
            "endedAtMs": self.ended_at_ms,
            "events": [serialize(event) for event in self.events],
            "transcript": self.transcript,
            "finalPrompt": stored_prompt,
            "composer": dict(self.composer_metadata),
            "error": self.error,
            "sourceApp": self.source_app,
        }
