from __future__ import annotations

from voice_context.core.anchors import reference_anchor_positions, render_anchored_transcript
from voice_context.core.session import VoiceSession


def test_repeated_demonstratives_keep_reference_anchors_in_order() -> None:
    session = VoiceSession()
    session.set_partial("把这个东西")
    first = session.add_reference("A", captured_at_ms=1000)
    session.set_partial("把这个东西和这个东西")
    second = session.add_reference("B", captured_at_ms=2000)
    session.set_partial("把这个东西和这个东西合并一下")

    positions = reference_anchor_positions(session.transcript, session.references)

    assert first and second
    assert [position for position, _ in positions] == [5, 10]
    rendered = render_anchored_transcript(session.transcript, session.references)
    assert rendered.index("[1]") < rendered.index("[2]")


def test_anchor_mapping_survives_revised_text_and_escapes_html() -> None:
    session = VoiceSession()
    session.set_partial("检查 <this>")
    session.add_reference("A", captured_at_ms=1000)
    session.set_partial("请检查 <this> & 修复")

    rendered = render_anchored_transcript(session.transcript, session.references)

    assert "[1]" in rendered
    assert "&lt;this&gt;" in rendered
    assert "&amp;" in rendered
    assert "<this>" not in rendered


def test_empty_and_identical_snapshots_produce_monotonic_anchors() -> None:
    session = VoiceSession()
    session.add_reference("A", captured_at_ms=0)
    session.set_partial("开始")
    session.add_reference("B", captured_at_ms=100)
    session.add_reference("C", captured_at_ms=200)

    positions = reference_anchor_positions(session.transcript, session.references)

    assert [position for position, _ in positions] == [0, 2, 2]
