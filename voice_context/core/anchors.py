from __future__ import annotations

from difflib import SequenceMatcher
from html import escape

from .events import ReferenceEvent


def reference_anchor_positions(
    transcript: str, references: list[ReferenceEvent]
) -> list[tuple[int, ReferenceEvent]]:
    positions: list[tuple[int, ReferenceEvent]] = []
    minimum = 0
    for reference in references:
        snapshot = reference.transcript_at_capture
        if not snapshot:
            position = minimum
        elif transcript.startswith(snapshot):
            position = len(snapshot)
        else:
            blocks = [
                block
                for block in SequenceMatcher(None, snapshot, transcript, autojunk=False).get_matching_blocks()
                if block.size
            ]
            if blocks:
                last = max(blocks, key=lambda block: block.a + block.size)
                position = last.b + last.size
            else:
                position = min(len(snapshot), len(transcript))
        position = max(minimum, min(position, len(transcript)))
        positions.append((position, reference))
        minimum = position
    return positions


def render_anchored_transcript(
    transcript: str,
    references: list[ReferenceEvent],
    *,
    max_chars: int = 240,
    highlighted_id: str = "",
) -> str:
    placements = reference_anchor_positions(transcript, references)
    start = max(0, len(transcript) - max_chars)
    visible = transcript[start:]
    markers: dict[int, list[tuple[int, ReferenceEvent]]] = {}
    for number, (position, reference) in enumerate(placements, start=1):
        if position < start:
            continue
        markers.setdefault(position - start, []).append((number, reference))

    parts = ["..." if start else ""]
    for index in range(len(visible) + 1):
        for number, reference in markers.get(index, []):
            background = "#3fa67f" if reference.id == highlighted_id else "#27775f"
            parts.append(
                f'<span style="background:{background}; color:#ffffff; font-weight:600;">'
                f"&nbsp;[{number}]&nbsp;</span>"
            )
        if index < len(visible):
            parts.append(escape(visible[index]))
    return "".join(parts)
