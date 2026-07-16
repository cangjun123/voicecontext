from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import QLineEdit

from voice_context.input.hotkeys import HotkeyManager


class HotkeyCaptureEdit(QLineEdit):
    """Read-only field that records one keyboard or mouse shortcut."""

    shortcut_captured = Signal(str)

    _MODIFIER_KEYS = {
        Qt.Key.Key_Control,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Shift,
        Qt.Key.Key_Meta,
        Qt.Key.Key_AltGr,
    }

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(HotkeyManager.normalize(value), parent)
        self._value_before_capture = self.text()
        self.setReadOnly(True)
        self.setPlaceholderText("Click, then press a shortcut")
        self.setToolTip("Click, then press a shortcut")

    def focusInEvent(self, event: QFocusEvent) -> None:
        self._value_before_capture = self.text()
        super().focusInEvent(event)
        self.selectAll()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in {Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton}:
            self._value_before_capture = self.text()
            button = "mouse4" if event.button() == Qt.MouseButton.BackButton else "mouse5"
            self._commit(self._with_modifiers(button, event.modifiers()))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._value_before_capture = self.text()
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.selectAll()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if key in self._MODIFIER_KEYS:
            event.accept()
            return
        if key == Qt.Key.Key_Escape and not modifiers:
            self.setText(self._value_before_capture)
            self.clearFocus()
            event.accept()
            return
        if key in {Qt.Key.Key_Backspace, Qt.Key.Key_Delete} and not modifiers:
            self._commit("")
            event.accept()
            return
        sequence = QKeySequence(event.keyCombination()).toString(
            QKeySequence.SequenceFormat.PortableText
        )
        if sequence == "+":
            sequence = "plus"
        elif sequence.endswith("++"):
            sequence = sequence[:-1] + "plus"
        shortcut = HotkeyManager.normalize(sequence)
        if shortcut:
            self._commit(shortcut)
        event.accept()

    @staticmethod
    def _with_modifiers(trigger: str, modifiers: Qt.KeyboardModifier) -> str:
        parts: list[str] = []
        for flag, name in (
            (Qt.KeyboardModifier.ControlModifier, "ctrl"),
            (Qt.KeyboardModifier.AltModifier, "alt"),
            (Qt.KeyboardModifier.ShiftModifier, "shift"),
            (Qt.KeyboardModifier.MetaModifier, "windows"),
        ):
            if modifiers & flag:
                parts.append(name)
        parts.append(trigger)
        return "+".join(parts)

    def _commit(self, shortcut: str) -> None:
        value = HotkeyManager.normalize(shortcut)
        self.setText(value)
        self.shortcut_captured.emit(value)
        self.clearFocus()
