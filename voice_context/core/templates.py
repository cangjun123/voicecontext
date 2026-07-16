from __future__ import annotations

DEFAULT_TEMPLATES: dict[str, str] = {
    "generic_markdown": """Task:
{{task}}

References:
{{references}}""",
    "codex_cli": "{{task}}",
    "claude_code": """## Task
{{task}}

## Referenced context
{{references}}

Inspect the repository and referenced context before making the smallest complete change.""",
    "cursor": """Implement this task in the current codebase:

{{task}}

Context:
{{references}}""",
    "chatgpt": """Act as a senior software engineer. Address the following task:

{{task}}

Use these references verbatim:
{{references}}""",
    "custom": """{{task}}

{{references}}""",
}


def render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered.strip()


def validate_template(template: str) -> tuple[bool, str]:
    if "{{task}}" not in template and "{{transcript}}" not in template:
        return False, "Template must contain {{task}} or {{transcript}}."
    return True, ""
