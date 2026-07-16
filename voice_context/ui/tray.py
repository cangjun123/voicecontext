from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .i18n import tr


def create_tray_icon(color: str = "#258263") -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("transparent"))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(5, 3, 22, 26, 6, 6)
    painter.setBrush(QColor("white"))
    painter.drawRoundedRect(13, 8, 6, 11, 3, 3)
    painter.drawRect(15, 18, 2, 6)
    painter.drawRoundedRect(10, 22, 12, 2, 1, 1)
    painter.end()
    return QIcon(pixmap)


class TrayIcon(QSystemTrayIcon):
    start_requested = Signal()
    finish_requested = Signal()
    pause_requested = Signal()
    show_requested = Signal()
    settings_requested = Signal()
    recent_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(create_tray_icon(), parent)
        self.setToolTip("VoiceContext - Idle")
        menu = QMenu()
        self.start_action = QAction(tr("Start listening"), self)
        self.finish_action = QAction(tr("Stop and compose"), self)
        self.pause_action = QAction(tr("Pause"), self)
        show_action = QAction(tr("Show window"), self)
        settings_action = QAction(tr("Settings"), self)
        recent_action = QAction(tr("Recent prompts"), self)
        exit_action = QAction(tr("Exit"), self)
        self.start_action.triggered.connect(self.start_requested)
        self.finish_action.triggered.connect(self.finish_requested)
        self.pause_action.triggered.connect(self.pause_requested)
        show_action.triggered.connect(self.show_requested)
        settings_action.triggered.connect(self.settings_requested)
        recent_action.triggered.connect(self.recent_requested)
        exit_action.triggered.connect(self.exit_requested)
        menu.addAction(self.start_action)
        menu.addAction(self.finish_action)
        menu.addAction(self.pause_action)
        menu.addSeparator()
        menu.addAction(show_action)
        menu.addAction(settings_action)
        menu.addAction(recent_action)
        menu.addSeparator()
        menu.addAction(exit_action)
        self.setContextMenu(menu)
        self.activated.connect(self._activated)
        self.set_state("Idle")

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_requested.emit()

    def set_state(self, state: str) -> None:
        active = state in {"Listening", "Paused"}
        self.start_action.setEnabled(not active)
        self.finish_action.setEnabled(active)
        self.pause_action.setEnabled(active)
        self.pause_action.setText(tr("Resume" if state == "Paused" else "Pause"))
        self.setToolTip(f"VoiceContext - {state}")
        color = "#d29433" if state == "Paused" else "#258263" if active else "#687078"
        self.setIcon(create_tray_icon(color))
