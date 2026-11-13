from __future__ import annotations

import pytest

from voice_context.core.events import SessionEvent
from voice_context.core.session import SessionState, VoiceSession, classify_content


def test_state_machine_and_transcript() -> None:
    session = VoiceSession()
    session.transition(SessionState.LISTENING)
    session.add_speech("first", 0, 100)
    session.set_partial("partial")
    assert session.transcript == "first partial"
    session.add_speech("second", 200, 300)
    assert session.transcript == "first second"
    session.transition(SessionState.COMPOSING)
    session.transition(SessionState.PROMPT_READY)
    with pytest.raises(ValueError):
        session.transition(SessionState.PAUSED)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("https://example.com/a", "url"),
        (r"C:\work\main.py", "file"),
        ("Error: invalid token", "error"),
        ("2026-01-02 10:11:12 started\n2026-01-02 10:11:13 stopped", "log"),
        ("def run():\n    return 1", "code"),
        ("ordinary note", "text"),
    ],
)
def test_content_classification(content: str, expected: str) -> None:
    assert classify_content(content) == expected


def test_reference_dedupe_undo_ignore_and_renumber() -> None:
    session = VoiceSession()
    session.set_partial("copy the first thing")
    first = session.add_reference("one\x00")
    assert first and first.id == "ref_1"
    assert first.content == "one"
    assert first.transcript_at_capture == "copy the first thing"
    assert session.add_reference("one") is None
    second = session.add_reference("two")
    assert second and second.id == "ref_2"
    assert session.remove_reference("ref_1") is first
    assert session.references[0].id == "ref_1"
    removed = session.ignore_last_capture()
    assert removed and removed.content == "two"
    assert session.add_reference("two") is None
    assert session.add_reference("two") is not None


def test_reference_privacy_redacts_prompt_and_event_content() -> None:
    session = VoiceSession()
    session.add_reference("TOP SECRET", content_type="text")
    session.final_prompt = "Reference:\nTOP SECRET"
    session.asr_metadata = {"status": "success", "provider": "aliyun_realtime"}
    session.composer_metadata = {"requested": True, "used": True, "request_id": "request-1"}
    session.output_metadata = {"clipboard_verified": True, "attempts": 1}
    data = session.to_dict(include_reference_content=False)
    assert "TOP SECRET" not in data["finalPrompt"]
    reference = next(event for event in data["events"] if event["type"] == "reference")
    assert "content" not in reference
    assert data["composer"] == {"requested": True, "used": True, "request_id": "request-1"}
    assert data["asr"]["provider"] == "aliyun_realtime"
    assert data["output"] == {"clipboard_verified": True, "attempts": 1}


def test_generic_event_uses_camel_case_timestamp() -> None:
    assert SessionEvent("state", 42, {"state": "Idle"}).to_dict() == {
        "type": "state",
        "timestampMs": 42,
        "payload": {"state": "Idle"},
    }


def test_cjk_transcript_joins_without_ascii_spaces_and_error_can_regenerate() -> None:
    session = VoiceSession()
    session.transition(SessionState.LISTENING)
    session.add_speech("先看这个函数。", 0, 500)
    session.add_speech("再补充单测。", 600, 1100)
    assert session.transcript == "先看这个函数。再补充单测。"

    session.transition(SessionState.COMPOSING)
    session.error = "LLM timed out"
    session.transition(SessionState.ERROR)
    assert session.can_regenerate is True

    session.transition(SessionState.COMPOSING)
    assert session.error == ""
    assert session.ended_at_ms is None
