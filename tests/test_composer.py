from __future__ import annotations

from voice_context.core.composer import ComposeOptions, RuleComposer
from voice_context.core.session import VoiceSession
from voice_context.core.templates import render_template, validate_template


def test_rule_composer_returns_one_natural_prompt_with_inline_reference() -> None:
    session = VoiceSession(source_app="terminal.exe")
    session.add_speech("帮我检查", 0, 900)
    session.add_reference("def f():\n    return `x`\n```", content_type="code", captured_at_ms=1000)
    session.add_speech("直接给 patch", 1200, 1700)
    output = RuleComposer().compose(session, ComposeOptions(target="codex_cli"))
    assert output.startswith("帮我检查 def f():")
    assert output.endswith("直接给 patch")
    assert "def f():\n    return `x`\n```" in output
    assert "Task:" not in output
    assert "References:" not in output


def test_template_supports_all_variables() -> None:
    template = "{{task}}|{{references}}|{{transcript}}|{{timestamp}}|{{source_app}}"
    values = {name: name.upper() for name in ("task", "references", "transcript", "timestamp", "source_app")}
    assert render_template(template, values) == "TASK|REFERENCES|TRANSCRIPT|TIMESTAMP|SOURCE_APP"
    assert validate_template(template) == (True, "")
    assert validate_template("{{references}}")[0] is False


def test_llm_rewrite_is_used_only_for_task_not_reference_body() -> None:
    class Rewriter:
        def rewrite(self, transcript, timeline, references):
            assert references[0].content == "immutable"
            return "Use [ref_1] exactly"

    session = VoiceSession()
    session.add_speech("spoken", 0, 100)
    session.add_reference("immutable", captured_at_ms=200)
    output = RuleComposer().compose(session, ComposeOptions(use_llm=True), Rewriter())
    assert output == "Use immutable exactly"


def test_two_repeated_demonstratives_resolve_to_nearby_clipboard_content() -> None:
    class Rewriter:
        def rewrite(self, transcript, timeline, references):
            assert transcript == "把这个东西 和这个东西合并一下"
            assert "1000 ms copied [ref_1]" in timeline
            assert "live transcript at copy: 把这个东西" in timeline
            assert "2200 ms copied [ref_2]" in timeline
            assert "live transcript at copy: 把这个东西 和这个东西" in timeline
            return "把[ref_1]和[ref_2]合并一下"

    session = VoiceSession()
    session.set_partial("把这个东西")
    session.add_reference("A", captured_at_ms=1000)
    session.set_partial("把这个东西 和这个东西")
    session.add_reference("B", captured_at_ms=2200)
    session.set_partial("")
    session.add_speech("把这个东西 和这个东西合并一下", 0, 3000)

    output = RuleComposer().compose(session, ComposeOptions(use_llm=True), Rewriter())
    assert output == "把A和B合并一下"


def test_template_single_pass_preserves_braces_in_references_and_uppercase_ref_markers() -> None:
    class UppercaseRewriter:
        def rewrite(self, transcript, timeline, references):
            return "Inspect [REF_1] and [Ref_2]"

    session = VoiceSession(source_app="Code.exe")
    session.add_speech("检查模板变量", 0, 500)
    session.add_reference("const tpl = '{{timestamp}} {{references}}';", captured_at_ms=600)
    session.add_reference("{{transcript}} literal", captured_at_ms=800)

    output = RuleComposer().compose(
        session,
        ComposeOptions(target="codex_cli", use_llm=True),
        UppercaseRewriter(),
    )
    assert output == "Inspect const tpl = '{{timestamp}} {{references}}'; and {{transcript}} literal"
