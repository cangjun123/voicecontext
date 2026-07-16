from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .events import ReferenceEvent, SpeechEvent
from .session import VoiceSession
from .templates import DEFAULT_TEMPLATES, render_template


class RewriteClient(Protocol):
    def rewrite(self, transcript: str, timeline: str, references: list[ReferenceEvent]) -> str: ...


@dataclass(slots=True)
class ComposeOptions:
    target: str = "codex_cli"
    custom_template: str = ""
    use_llm: bool = False
    preserve_chinese: bool = True
    source_app: str = ""


class RuleComposer:
    def build_task(self, session: VoiceSession) -> str:
        chunks: list[str] = []
        for event in session.sorted_content_events():
            if isinstance(event, SpeechEvent) and event.is_final:
                chunks.append(event.text.strip())
            elif isinstance(event, ReferenceEvent):
                chunks.append(f"[{event.id}]")
        if not any(event.is_final and event.text.strip() for event in session.speech_events):
            return "Review the captured references and determine the required change."
        task = " ".join(chunk for chunk in chunks if chunk)
        task = re.sub(r"\s+([，。！？,.!?])", r"\1", task)
        task = re.sub(r"\s+", " ", task).strip()
        return task or "Review the referenced context and determine the required change."

    def timeline_text(self, session: VoiceSession) -> str:
        lines: list[str] = []
        for event in session.sorted_content_events():
            if isinstance(event, SpeechEvent):
                if event.is_final and event.text.strip():
                    lines.append(
                        f"{event.start_ms}-{event.end_ms} ms speech: {event.text.strip()}"
                    )
            else:
                snapshot = event.transcript_at_capture or "(no live transcript yet)"
                lines.append(
                    f"{event.captured_at_ms} ms copied [{event.id}] {event.name}; "
                    f"live transcript at copy: {snapshot}"
                )
        return "\n".join(lines)

    @staticmethod
    def materialize_references(task: str, references: list[ReferenceEvent]) -> str:
        by_id = {reference.id: reference.content for reference in references}
        return re.sub(
            r"\[(ref_\d+)\]",
            lambda match: by_id.get(match.group(1), match.group(0)),
            task,
            flags=re.IGNORECASE,
        )

    def references_markdown(self, references: list[ReferenceEvent]) -> str:
        if not references:
            return "(No clipboard references captured.)"
        blocks: list[str] = []
        for ref in references:
            source = ""
            if ref.source_process or ref.source_window:
                details = " · ".join(filter(None, (ref.source_process, ref.source_window)))
                source = f"\nSource: {details}"
            language = self._code_fence_language(ref)
            fence = self._fence_for(ref.content)
            blocks.append(
                f"[{ref.id}] {ref.name} ({ref.content_type}){source}\n"
                f"{fence}{language}\n{ref.content}\n{fence}"
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _code_fence_language(ref: ReferenceEvent) -> str:
        if ref.content_type == "url":
            return "text"
        if ref.content_type in {"error", "log", "file", "text"}:
            return "text"
        return ""

    @staticmethod
    def _fence_for(content: str) -> str:
        longest = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
        return "`" * max(3, longest + 1)

    def compose(
        self,
        session: VoiceSession,
        options: ComposeOptions,
        rewrite_client: RewriteClient | None = None,
    ) -> str:
        task = self.build_task(session)
        if options.use_llm and rewrite_client is not None:
            task = rewrite_client.rewrite(
                transcript=session.transcript,
                timeline=self.timeline_text(session),
                references=session.references,
            ).strip() or task
        task = self.materialize_references(task, session.references)
        template = options.custom_template or DEFAULT_TEMPLATES.get(
            options.target, DEFAULT_TEMPLATES["generic_markdown"]
        )
        values = {
            "task": task,
            "references": self.references_markdown(session.references),
            "transcript": session.transcript,
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_app": options.source_app or session.source_app,
        }
        return render_template(template, values)
