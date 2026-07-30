from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

from PySide6.QtTest import QSignalSpy

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
    results = manager.register(
        {
            "hold_to_talk": "",
            "toggle_listening": "ctrl+alt+v",
            "finish": "ctrl+alt+v",
        }
    )

    assert all(result.success for result in results)
    assert len(observed) + len(manager._native_hotkeys) == 1
    manager.close()


def test_lost_hold_release_is_recovered(monkeypatch, qapp) -> None:
    fake_keyboard = SimpleNamespace(is_pressed=lambda key: False)
    monkeypatch.setattr(hotkey_module, "keyboard", fake_keyboard)
    manager = HotkeyManager()
    manager._hold_combination = "ctrl+alt+space"
    released = QSignalSpy(manager.hold_released)
    manager._hold_press()
    assert manager._hold_down is True
    manager._check_hold_state()
    assert manager._hold_down is False
    assert released.count() == 1
    manager.close()


def test_duplicate_native_delivery_triggers_an_action_once(qapp) -> None:
    manager = HotkeyManager()
    triggered = QSignalSpy(manager.toggle_requested)
    manager._native_hotkeys[321] = (manager.toggle_requested.emit, "f23")
    message = wintypes.MSG()
    message.message = 0x0312
    message.wParam = 321
    message.lParam = 1
    message.time = 123
    address = ctypes.addressof(message)
    manager._handle_native_message(address)
    manager._handle_native_message(address)
    assert triggered.count() == 1
    manager._native_hotkeys.clear()
    manager.close()
