from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from .i18n import tr, translate_widget_tree


class OverlayWindow(QWidget):
    COLORS = {
        "Idle": "#778089",
        "Listening": "#28a175",
        "Paused": "#d29433",
        "Composing": "#5685c7",
        "PromptReady": "#28a175",
        "Error": "#c85a5a",
    }

    def __init__(self, config: dict) -> None:
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setObjectName("overlay")
        self.config = config
        self._drag_origin: QPoint | None = None
        self._pinned_position = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.setFixedWidth(460)
        self.setMaximumHeight(250)

        self.frame = QFrame()
        self.frame.setObjectName("overlayFrame")
        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(15, 12, 15, 12)
        layout.setSpacing(7)
        header = QHBoxLayout()
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.status = QLabel("Idle")
        self.status.setStyleSheet("font-weight: 650; font-size: 12px;")
        self.timer_label = QLabel("")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        header.addWidget(self.dot)
        header.addWidget(self.status)
        header.addStretch()
        header.addWidget(self.timer_label)
        layout.addLayout(header)
        self.transcript = QLabel("")
        self.transcript.setWordWrap(True)
        self.transcript.setMaximumHeight(82)
        self.transcript.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.transcript)
        self.references = QLabel("")
        self.references.setWordWrap(True)
        self.references.setStyleSheet("color: #aeb5bd; font-size: 11px;")
        layout.addWidget(self.references)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(3)
        layout.addWidget(self.level)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self.frame)
        self.setWindowOpacity(float(config.get("overlay_opacity", 0.94)))
        self._apply_style("Idle")
        translate_widget_tree(self)
        for child in self.findChildren(QWidget):
            child.installEventFilter(self)

    def _apply_style(self, state: str) -> None:
        color = self.COLORS.get(state, self.COLORS["Idle"])
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.frame.setStyleSheet(
            "QFrame#overlayFrame { background: rgba(30, 33, 37, 242); "
            "border: 1px solid rgba(255,255,255,35); border-radius: 7px; } "
            "QLabel { color: #f2f3f4; } QProgressBar { background: #45494e; border: none; } "
            f"QProgressBar::chunk {{ background: {color}; }}"
        )

    def set_state(self, state: str, detail: str = "") -> None:
        labels = {"PromptReady": "Prompt copied", "Composing": "Composing"}
        self.status.setText(tr(labels.get(state, state)))
        self._apply_style(state)
        if detail:
            self.transcript.setText(tr(detail))
        self.level.setVisible(state == "Listening")
        self._hide_timer.stop()
        self.show_without_focus()
        if state in {"PromptReady", "Error", "Idle"}:
            self._hide_timer.start(int(self.config.get("overlay_duration_ms", 2600)))

    def set_transcript(self, text: str) -> None:
        value = text.strip()
        if len(value) > 240:
            value = "..." + value[-237:]
        self.transcript.setText(value or "Listening for speech...")

    def set_references(self, references: list) -> None:
        if not references:
            self.references.setText(tr("References: none"))
            return
        labels = "  ".join(f"[{ref.id}] {ref.name}" for ref in references[-3:])
        prefix = f"{tr('References')} ({len(references)}): "
        self.references.setText(prefix + labels)

    def show_capture(self, reference, sensitive: bool = False) -> None:
        label = tr("Sensitive content detected") if sensitive else f"{tr('Captured')} {reference.id}"
        self.status.setText(label)
        self.transcript.setText(reference.preview)
        self._apply_style("Error" if sensitive else "Listening")
        self.show_without_focus()
        QTimer.singleShot(1300, lambda: self.status.setText(tr("Listening")) if self.isVisible() else None)

    def set_level(self, value: float) -> None:
        self.level.setValue(int(max(0.0, min(1.0, value)) * 100))

    def set_elapsed(self, milliseconds: int) -> None:
        seconds = milliseconds // 1000
        self.timer_label.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")

    def show_without_focus(self) -> None:
        if not self._pinned_position:
            self.reposition()
        self.show()
        self.raise_()

    def reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        margin = 24
        position = self.config.get("overlay_position", "bottom_center")
        if position == "bottom_right":
            x = area.right() - self.width() - margin
            y = area.bottom() - self.height() - margin
        elif position == "top_center":
            x = area.center().x() - self.width() // 2
            y = area.top() + margin
        elif position == "top_right":
            x = area.right() - self.width() - margin
            y = area.top() + margin
        else:
            x = area.center().x() - self.width() // 2
            y = area.bottom() - self.height() - margin
        self.move(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_origin and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            self._pinned_position = True
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            return True
        return super().eventFilter(watched, event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            self._pinned_position = True

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_origin = None
