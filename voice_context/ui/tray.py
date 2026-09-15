from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .i18n import tr
from voice_context.core.session import SessionState


LOG = logging.getLogger("voice_context")


def create_tray_icon(color: str = "#0d9488", inactive: bool = False) -> QIcon:
    # Standard Windows tray icon: 32x32 pixels, with padding for safe area
    # Windows will scale down to 16x16 for display, so we design at 32 for clarity
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    base_size = 32
    size = int(base_size * ratio)
    pixmap = QPixmap(size, size)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    # Theme-consistent colors
    if inactive:
        bg_color = QColor("#4b5563")
    else:
        bg_color = QColor(color)
    
    # Standard tray icon with padding (safe area)
    # Leave 4px margin on all sides for the "safe zone"
    margin = int(4 * ratio)
    icon_size = size - (margin * 2)
    
    # Draw background circle (standard for tray icons)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(bg_color)
    painter.drawEllipse(margin, margin, icon_size, icon_size)
    
    # Draw microphone icon in the center
    painter.setBrush(QColor("white"))
    center_x = size / 2
    center_y = size / 2
    
    # Mic proportions based on 32px base
    mic_width = int(8 * ratio)
    mic_height = int(12 * ratio)
    mic_top = center_y - (mic_height / 2) - int(2 * ratio)
    
    # Mic head (rounded rect)
    painter.drawRoundedRect(
        int(center_x - mic_width / 2),
        int(mic_top),
        mic_width,
        mic_height,
        int(4 * ratio),
        int(4 * ratio)
    )
    
    # Mic stem
    stem_width = int(2 * ratio)
    stem_height = int(4 * ratio)
    stem_top = mic_top + mic_height
    painter.drawRect(
        int(center_x - stem_width / 2),
        int(stem_top),
        stem_width,
        stem_height
    )
    
    # Mic base (horizontal line)
    base_width = int(12 * ratio)
    base_height = int(2 * ratio)
    base_top = stem_top + stem_height
    painter.drawRoundedRect(
        int(center_x - base_width / 2),
        int(base_top),
        base_width,
        base_height,
        int(1 * ratio),
        int(1 * ratio)
    )
    
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
        LOG.info("Tray activated: %s", reason.name)
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_requested.emit()

    def set_state(self, state: str) -> None:
        try:
            session_state = SessionState(state)
        except ValueError:
            session_state = SessionState.IDLE
        active = session_state in {SessionState.LISTENING, SessionState.PAUSED}
        busy = session_state in {SessionState.ARMED, SessionState.COMPOSING}
        self.start_action.setEnabled(not active and not busy)
        self.finish_action.setEnabled(active)
        self.pause_action.setEnabled(active)
        self.pause_action.setText(tr("Resume" if session_state == SessionState.PAUSED else "Pause"))
        self.setToolTip(f"VoiceContext - {state}")
        
        # Use theme-consistent accent color for active states
        if session_state == SessionState.PAUSED:
            color = "#d97706"  # warning amber
            inactive = False
        elif active:
            color = "#0d9488"  # accent teal
            inactive = False
        elif busy:
            color = "#2563eb"  # info blue
            inactive = False
        else:
            color = "#4b5563"  # darker neutral gray
            inactive = True
            
        self.setIcon(create_tray_icon(color, inactive))
