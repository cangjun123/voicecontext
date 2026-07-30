from __future__ import annotations

import ctypes
import os
import threading
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, QTimer, Signal

try:
    import keyboard
except ImportError:  # pragma: no cover
    keyboard = None

try:
    import mouse
except ImportError:  # pragma: no cover
    mouse = None


@dataclass(slots=True)
class HotkeyRegistrationResult:
    action: str
    combination: str
    backend: str
    success: bool
    error: str = ""

    def message(self) -> str:
        label = self.action.replace("_", " ").title()
        return f"{label} ({self.combination}): {self.error}"


class _WindowsHotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: HotkeyManager) -> None:
        super().__init__()
        self.owner = owner

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        self.owner._handle_native_message(message)
        return False, 0


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
    action_triggered = Signal(str)
    registration_error = Signal(str)
    registration_changed = Signal(object)

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
    _owned_native_combinations: set[str] = set()

    def __init__(self, window_handle: int = 0) -> None:
        super().__init__()
        self._window_handle = window_handle
        self._keyboard_handles: list[Any] = []
        self._mouse_handles: list[Any] = []
        self._native_hotkeys: dict[int, tuple[Any, str]] = {}
        self._registered_config: dict[str, str] = {}
        self._registration_results: list[HotkeyRegistrationResult] = []
        self._next_native_id = 0xB500
        self._suspended = False
        self._native_filter_installed = False
        self._native_filter: _WindowsHotkeyEventFilter | None = None
        self._last_native_message: tuple[int, int, int] | None = None
        self._hold_combination = ""
        self._hold_down = False
        self._lock = threading.Lock()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._check_hold_state)
        app = QCoreApplication.instance()
        if app is not None:
            self._health_timer.start()
        if os.name == "nt" and app is not None:
            self._native_filter = _WindowsHotkeyEventFilter(self)
            app.installNativeEventFilter(self._native_filter)
            self._native_filter_installed = True

    @property
    def available(self) -> bool:
        return keyboard is not None

    @property
    def suspended(self) -> bool:
        return self._suspended

    @property
    def registration_results(self) -> list[HotkeyRegistrationResult]:
        return list(self._registration_results)

    def register(self, hotkeys: dict[str, str]) -> list[HotkeyRegistrationResult]:
        self.recover()
        self.unregister()
        self._registered_config = dict(hotkeys)
        self._suspended = False
        results: list[HotkeyRegistrationResult] = []
        hold = self.normalize(hotkeys.get("hold_to_talk", ""))
        self._hold_combination = hold
        if hold:
            try:
                if self._is_mouse_combo(hold):
                    self._register_mouse(hold, self._hold_press, self._hold_release)
                    backend = "mouse_hook"
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
                    backend = "keyboard_hook"
                results.append(HotkeyRegistrationResult("hold_to_talk", hold, backend, True))
            except Exception as exc:
                results.append(
                    HotkeyRegistrationResult("hold_to_talk", hold, "hook", False, str(exc))
                )
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
                results.append(
                    HotkeyRegistrationResult(
                        name,
                        combination,
                        "internal",
                        False,
                        "conflicts with another VoiceContext shortcut",
                    )
                )
                continue
            used.add(combination)
            try:
                signal = getattr(self, signal_name)

                def callback(current=name, target=signal):
                    self._emit_action(current, target)

                if self._is_mouse_combo(combination):
                    self._register_mouse(combination, callback)
                    backend = "mouse_hook"
                elif (
                    os.name == "nt"
                    and self._native_filter_installed
                    and self._window_handle
                ):
                    self._register_native(combination, callback)
                    backend = "register_hotkey"
                else:
                    available, detail = self.availability(combination)
                    if not available:
                        raise ValueError(detail)
                    self._keyboard_handles.append(keyboard.add_hotkey(combination, callback))
                    backend = "keyboard_hook"
                results.append(HotkeyRegistrationResult(name, combination, backend, True))
            except Exception as exc:
                results.append(
                    HotkeyRegistrationResult(name, combination, "registration", False, str(exc))
                )
        self._registration_results = results
        failures = [result.message() for result in results if not result.success]
        if failures:
            self.registration_error.emit("\n".join(failures))
        self.registration_changed.emit(self.registration_results)
        return self.registration_results

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
        self.action_triggered.emit("hold_to_talk_pressed")
        self.hold_started.emit()

    def _hold_release(self) -> None:
        with self._lock:
            if not self._hold_down:
                return
            self._hold_down = False
        self.action_triggered.emit("hold_to_talk_released")
        self.hold_released.emit()

    def _emit_action(self, action: str, signal) -> None:
        self.action_triggered.emit(action)
        signal.emit()

    def suspend(self) -> None:
        if self._suspended:
            return
        self.recover()
        self.unregister()
        self._suspended = True

    def resume(self) -> list[HotkeyRegistrationResult]:
        if not self._suspended:
            return self.registration_results
        return self.register(self._registered_config)

    def recover(self) -> None:
        with self._lock:
            was_down = self._hold_down
            self._hold_down = False
        if was_down:
            self.hold_released.emit()

    def _check_hold_state(self) -> None:
        if not self._hold_down or not self._hold_combination or keyboard is None:
            return
        if self._is_mouse_combo(self._hold_combination):
            return
        try:
            pressed = all(
                keyboard.is_pressed(part) for part in self._hold_combination.split("+")
            )
        except Exception:
            return
        if not pressed:
            self.recover()

    def _handle_native_message(self, message) -> None:
        if os.name != "nt":
            return
        try:
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312:  # WM_HOTKEY
                signature = (int(msg.wParam), int(msg.lParam), int(msg.time))
                if signature == self._last_native_message:
                    return
                self._last_native_message = signature
                registration = self._native_hotkeys.get(signature[0])
                if registration is not None:
                    callback, _ = registration
                    callback()
        except (TypeError, ValueError):
            pass

    def _register_native(self, combination: str, callback) -> None:
        spec = self._native_spec(combination)
        if spec is None:
            raise ValueError("This shortcut cannot be registered by Windows")
        modifiers, virtual_key = spec
        identifier = self._next_native_id
        self._next_native_id += 1
        user32 = self._windows_user32()
        ctypes.set_last_error(0)
        if not user32.RegisterHotKey(
            self._window_handle, identifier, modifiers | 0x4000, virtual_key
        ):
            error = ctypes.get_last_error()
            detail = "Already occupied by another application" if error == 1409 else f"Windows error {error}"
            raise ValueError(detail)
        self._native_hotkeys[identifier] = (callback, combination)
        self._owned_native_combinations.add(combination)

    def unregister(self) -> None:
        if os.name == "nt" and self._native_hotkeys:
            user32 = self._windows_user32()
            for identifier, (_, combination) in tuple(self._native_hotkeys.items()):
                user32.UnregisterHotKey(self._window_handle, identifier)
                self._owned_native_combinations.discard(combination)
            self._native_hotkeys.clear()
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

    def close(self) -> None:
        self.unregister()
        self._health_timer.stop()
        app = QCoreApplication.instance()
        if self._native_filter_installed and app is not None and self._native_filter is not None:
            app.removeNativeEventFilter(self._native_filter)
            self._native_filter_installed = False
            self._native_filter = None

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
        if normalized in HotkeyManager._owned_native_combinations:
            return True, "Currently registered by VoiceContext"
        spec = HotkeyManager._native_spec(normalized)
        if spec is None:
            return True, "Syntax valid; occupancy cannot be checked for this key"
        modifiers, virtual_key = spec
        identifier = 0xB45E
        user32 = HotkeyManager._windows_user32()
        registered = bool(user32.RegisterHotKey(None, identifier, modifiers | 0x4000, virtual_key))
        if registered:
            user32.UnregisterHotKey(None, identifier)
            return True, "Available globally"
        return False, "Already occupied by another application"

    @staticmethod
    def _windows_user32():
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = (
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        )
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
        user32.UnregisterHotKey.restype = wintypes.BOOL
        return user32

    @staticmethod
    def _native_spec(value: str) -> tuple[int, int] | None:
        parts = value.split("+")
        if not parts:
            return None
        modifiers = 0
        modifier_codes = {
            "alt": 0x0001,
            "ctrl": 0x0002,
            "shift": 0x0004,
            "windows": 0x0008,
        }
        for part in parts[:-1]:
            if part not in modifier_codes:
                return None
            modifiers |= modifier_codes[part]
        key = parts[-1]
        special = {
            "space": 0x20,
            "enter": 0x0D,
            "esc": 0x1B,
            "backspace": 0x08,
            "tab": 0x09,
            "delete": 0x2E,
            "insert": 0x2D,
            "home": 0x24,
            "end": 0x23,
            "page up": 0x21,
            "page down": 0x22,
            "up": 0x26,
            "down": 0x28,
            "left": 0x25,
            "right": 0x27,
            "plus": 0xBB,
            "minus": 0xBD,
            "comma": 0xBC,
            "period": 0xBE,
        }
        if len(key) == 1 and key.isalnum():
            virtual_key = ord(key.upper())
        elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
            virtual_key = 0x70 + int(key[1:]) - 1
        else:
            virtual_key = special.get(key)
        return (modifiers, virtual_key) if virtual_key is not None else None
