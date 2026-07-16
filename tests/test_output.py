from __future__ import annotations

from voice_context.output.dispatcher import OutputDispatcher


class FakeClipboard:
    def __init__(self, succeeds_on: int | None) -> None:
        self.succeeds_on = succeeds_on
        self.attempts = 0
        self.value = ""

    def setText(self, text, mode) -> None:
        self.attempts += 1
        self.value = text if self.succeeds_on and self.attempts >= self.succeeds_on else "other"

    def text(self, mode) -> str:
        return self.value


def test_clipboard_verification_succeeds_immediately() -> None:
    clipboard = FakeClipboard(succeeds_on=1)
    results = []

    OutputDispatcher(clipboard).copy_verified("prompt", results.append)

    assert results[0].success is True
    assert results[0].attempts == 1


def test_clipboard_verification_retries_then_succeeds(qtbot) -> None:
    clipboard = FakeClipboard(succeeds_on=2)
    results = []

    OutputDispatcher(clipboard).copy_verified("prompt", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=1000)

    assert results[0].success is True
    assert results[0].attempts == 2


def test_clipboard_verification_reports_failure_after_three_attempts(qtbot) -> None:
    clipboard = FakeClipboard(succeeds_on=None)
    results = []

    OutputDispatcher(clipboard).copy_verified("prompt", results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=1000)

    assert results[0].success is False
    assert results[0].attempts == 3
    assert "did not match" in results[0].error


def test_new_clipboard_write_supersedes_pending_retry(qtbot) -> None:
    clipboard = FakeClipboard(succeeds_on=3)
    first_results = []
    second_results = []
    dispatcher = OutputDispatcher(clipboard)

    dispatcher.copy_verified("first", first_results.append)
    dispatcher.copy_verified("second", second_results.append)
    qtbot.waitUntil(lambda: bool(second_results), timeout=1000)

    assert first_results == []
    assert second_results[0].success is True
    assert clipboard.value == "second"
