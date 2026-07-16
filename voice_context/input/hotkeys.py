from __future__ import annotations

import ctypes
import os
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

try:
    import keyboard
except ImportError:  # pragma: no cover
    keyboard = None

try:
    import mouse
except ImportError:  # pragma: no cover
    mouse = None


class HotkeyManager(QObject):
    hold_started = Signal()
    hold_released = Signal()
    toggle_requested = Signal()
    finish_requested = Signal()
    cancel_requested = Signal()
    pause_requested = Signal()
    undo_reference_requested = Signal()
    ignore_reference_requested = Signal()
    overlay_requested = Signal()
    recopy_requested = Signal()
    registration_error = Signal(str)

    SIGNALS = {
        "toggle_listening": "toggle_requested",
        "finish": "finish_requested",
        "cancel": "cancel_requested",
        "pause_resume": "pause_requested",
        "undo_reference": "undo_reference_requested",
        "ignore_reference": "ignore_reference_requested",
        "toggle_overlay": "overlay_requested",
        "recopy_prompt": "recopy_requested",
    }
    SHAREABLE_ACTIONS = frozenset({"toggle_listening", "finish"})

    def __init__(self) -> None:
        super().__init__()
        self._keyboard_handles: list[Any] = []
        self._mouse_handles: list[Any] = []
        self._hold_down = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return keyboard is not None

    def register(self, hotkeys: dict[str, str]) -> list[str]:
        self.unregister()
        if keyboard is None:
            message = "The keyboard package is not installed; global hotkeys are disabled."
            self.registration_error.emit(message)
            return [message]
        errors: list[str] = []
        hold = self.normalize(hotkeys.get("hold_to_talk", ""))
        if hold:
            try:
                if self._is_mouse_combo(hold):
                    self._register_mouse(hold, self._hold_press, self._hold_release)
                else:
                    available, detail = self.availability(hold)
                    if not available:
                        raise ValueError(detail)
                    self._keyboard_handles.append(
                        keyboard.add_hotkey(hold, self._hold_press, trigger_on_release=False)
                    )
                    self._keyboard_handles.append(
                        keyboard.add_hotkey(hold, self._hold_release, trigger_on_release=True)
                    )
            except Exception as exc:
                errors.append(f"Hold-to-talk ({hold}): {exc}")
        groups = self._groups(hotkeys)
        shared_combinations = {
            combination
            for combination, names in groups.items()
            if set(names) == self.SHAREABLE_ACTIONS
        }
        used = {hold} if hold else set()
        for name, signal_name in self.SIGNALS.items():
            combination = self.normalize(hotkeys.get(name, ""))
            if not combination:
                continue
            # toggle_session already starts while idle and finishes while listening, so a
            # shared Finish binding must not be registered as a second callback.
            if name == "finish" and combination in shared_combinations:
                continue
            if combination in used:
                errors.append(f"{name}: conflicts with another VoiceContext shortcut ({combination})")
                continue
            used.add(combination)
            try:
                signal = getattr(self, signal_name)
                if self._is_mouse_combo(combination):
                    self._register_mouse(combination, signal.emit)
                else:
                    available, detail = self.availability(combination)
                    if not available:
                        raise ValueError(detail)
                    self._keyboard_handles.append(keyboard.add_hotkey(combination, signal.emit))
            except Exception as exc:
                errors.append(f"{name} ({combination}): {exc}")
        if errors:
            self.registration_error.emit("\n".join(errors))
        return errors

    @classmethod
    def duplicate_conflicts(cls, hotkeys: dict[str, str]) -> list[str]:
        return sorted(
            combination
            for combination, names in cls._groups(hotkeys).items()
            if len(names) > 1 and set(names) != cls.SHAREABLE_ACTIONS
        )

    @classmethod
    def _groups(cls, hotkeys: dict[str, str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for name, value in hotkeys.items():
            combination = cls.normalize(value)
            if combination:
                groups.setdefault(combination, []).append(name)
        return groups

    def _hold_press(self) -> None:
        with self._lock:
            if self._hold_down:
                return
            self._hold_down = True
        self.hold_started.emit()

    def _hold_release(self) -> None:
        with self._lock:
            if not self._hold_down:
                return
            self._hold_down = False
        self.hold_released.emit()

    def unregister(self) -> None:
        if keyboard is not None:
            for handle in self._keyboard_handles:
                try:
                    keyboard.remove_hotkey(handle)
                except (KeyError, ValueError):
                    pass
        if mouse is not None:
            for handle in self._mouse_handles:
                try:
                    mouse.unhook(handle)
                except (KeyError, ValueError):
                    pass
        self._keyboard_handles.clear()
        self._mouse_handles.clear()
        self._hold_down = False

    def _register_mouse(
        self, combination: str, press_callback, release_callback=None
    ) -> None:
        if mouse is None:
            raise ValueError("mouse package is not installed")
        parts = combination.split("+")
        button_token = next(part for part in parts if part in {"mouse4", "mouse5"})
        button = mouse.X if button_token == "mouse4" else mouse.X2
        modifiers = [part for part in parts if part != button_token]

        def pressed() -> None:
            if keyboard is not None and all(keyboard.is_pressed(key) for key in modifiers):
                press_callback()

        self._mouse_handles.append(
            mouse.on_button(pressed, buttons=(button,), types=(mouse.DOWN,))
        )
        if release_callback is not None:
            self._mouse_handles.append(
                mouse.on_button(release_callback, buttons=(button,), types=(mouse.UP,))
            )

    @staticmethod
    def normalize(value: str) -> str:
        aliases = {
            "control": "ctrl",
            "return": "enter",
            "escape": "esc",
            "meta": "windows",
            "win": "windows",
            "super": "windows",
            "del": "delete",
            "ins": "insert",
            "pgup": "page up",
            "pageup": "page up",
            "pgdown": "page down",
            "pagedown": "page down",
            "xbutton1": "mouse4",
            "xbutton2": "mouse5",
        }
        prepared = value.strip().lower().replace("page up", "pageup").replace("page down", "pagedown")
        parts = [part.strip() for part in prepared.replace(" ", "+").split("+") if part.strip()]
        return "+".join(aliases.get(part, part) for part in parts)

    @staticmethod
    def validate(value: str) -> tuple[bool, str]:
        normalized = HotkeyManager.normalize(value)
        if not normalized:
            return True, ""
        if keyboard is None:
            return False, "keyboard package is not installed"
        if HotkeyManager._is_mouse_combo(normalized):
            parts = normalized.split("+")
            mouse_keys = [part for part in parts if part in {"mouse4", "mouse5"}]
            if len(mouse_keys) != 1 or parts[-1] not in {"mouse4", "mouse5"}:
                return False, "Use one mouse side button as the final trigger key"
            modifiers = [part for part in parts if part not in {"mouse4", "mouse5"}]
            if any(part not in {"ctrl", "alt", "shift", "windows"} for part in modifiers):
                return False, "Mouse shortcuts support Ctrl, Alt, Shift, or Windows modifiers"
            return (mouse is not None, "Available mouse shortcut" if mouse is not None else "mouse package is not installed")
        try:
            keyboard.parse_hotkey(normalized)
            return True, "Available syntax"
        except (ValueError, KeyError) as exc:
            return False, str(exc)

    @staticmethod
    def _is_mouse_combo(value: str) -> bool:
        return any(part in {"mouse4", "mouse5"} for part in value.split("+"))

    @staticmethod
    def availability(value: str) -> tuple[bool, str]:
        normalized = HotkeyManager.normalize(value)
        valid, detail = HotkeyManager.validate(normalized)
        if not valid:
            return False, detail
        if not normalized or HotkeyManager._is_mouse_combo(normalized) or os.name != "nt":
            return True, "Available"
        parts = normalized.split("+")
        modifiers = 0
        modifier_codes = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "windows": 0x0008}
        for part in parts[:-1]:
            if part not in modifier_codes:
                return True, "Syntax valid; occupancy cannot be checked for this key"
            modifiers |= modifier_codes[part]
        key = parts[-1]
        special = {
            "space": 0x20, "enter": 0x0D, "esc": 0x1B, "backspace": 0x08,
            "tab": 0x09, "delete": 0x2E, "insert": 0x2D, "home": 0x24,
            "end": 0x23, "page up": 0x21, "page down": 0x22,
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        }
        if len(key) == 1 and key.isalnum():
            virtual_key = ord(key.upper())
        elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            virtual_key = 0x70 + int(key[1:]) - 1
        else:
            virtual_key = special.get(key)
        if virtual_key is None:
            return True, "Syntax valid; occupancy cannot be checked for this key"
        identifier = 0xB45E
        user32 = ctypes.windll.user32
        registered = bool(user32.RegisterHotKey(None, identifier, modifiers, virtual_key))
        if registered:
            user32.UnregisterHotKey(None, identifier)
            return True, "Available globally"
        return False, "Already occupied by another application"
