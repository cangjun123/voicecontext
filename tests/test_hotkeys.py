from __future__ import annotations

from types import SimpleNamespace

import voice_context.input.hotkeys as hotkey_module
from voice_context.input.hotkeys import HotkeyManager


def test_toggle_and_finish_are_the_only_shareable_actions() -> None:
    assert HotkeyManager.duplicate_conflicts(
        {"toggle_listening": "ctrl+alt+v", "finish": "Ctrl + Alt + V"}
    ) == []
    assert HotkeyManager.duplicate_conflicts(
        {"toggle_listening": "ctrl+alt+v", "cancel": "ctrl+alt+v"}
    ) == ["ctrl+alt+v"]


def test_shared_toggle_and_finish_register_only_one_callback(monkeypatch) -> None:
    observed: list[str] = []
    fake_keyboard = SimpleNamespace(
        add_hotkey=lambda combination, callback, **kwargs: observed.append(combination) or object(),
        remove_hotkey=lambda handle: None,
    )
    monkeypatch.setattr(hotkey_module, "keyboard", fake_keyboard)
    monkeypatch.setattr(HotkeyManager, "availability", staticmethod(lambda value: (True, "Available")))

    manager = HotkeyManager()
    errors = manager.register(
        {
            "hold_to_talk": "",
            "toggle_listening": "ctrl+alt+v",
            "finish": "ctrl+alt+v",
        }
    )

    assert errors == []
    assert observed == ["ctrl+alt+v"]
