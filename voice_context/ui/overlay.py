from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from voice_context.core.anchors import render_anchored_transcript
from voice_context.core.session import VoiceSession

from .i18n import tr, translate_widget_tree


class OverlayWindow(QWidget):
    edit_requested = Signal()

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
        self._state = "Idle"
        self._drag_origin: QPoint | None = None
        self._press_position: QPoint | None = None
        self._dragging = False
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
        self.transcript.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.transcript.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Scrollable transcript: long speech keeps the overlay compact while the
        # view auto-follows the newest words instead of clipping them.
        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setWidget(self.transcript)
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setFixedHeight(82)
        self.transcript_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(self.transcript_scroll)
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
        self._state = state
        labels = {"PromptReady": "Prompt copied", "Composing": "Composing"}
        self.status.setText(tr(labels.get(state, state)))
        self._apply_style(state)
        if detail:
            self.transcript.setTextFormat(Qt.TextFormat.PlainText)
            self.transcript.setText(tr(detail))
        self.level.setVisible(state == "Listening")
        self._hide_timer.stop()
        self.show_without_focus()
        if state in {"PromptReady", "Error", "Idle"}:
            self._hide_timer.start(int(self.config.get("overlay_duration_ms", 2600)))

    def set_transcript(self, text: str) -> None:
        self.transcript.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript.setText(text.strip() or tr("Listening for speech..."))
        self._scroll_transcript_to_latest()

    def _scroll_transcript_to_latest(self) -> None:
        """Keep the newest words visible as the transcript grows."""
        # A queued singleShot defers the scroll one event-loop turn, so the label
        # relayout finishes first. Calling processEvents() here instead would run a
        # nested loop that dispatches the NEXT queued transcript update, re-entering
        # this method until the stack overflows.
        QTimer.singleShot(0, self._apply_latest_scroll)

    def _apply_latest_scroll(self) -> None:
        bar = self.transcript_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _scroll_transcript_to_top(self) -> None:
        """Show the start of the completed prompt, with manual scroll to read on."""
        bar = self.transcript_scroll.verticalScrollBar()
        bar.setValue(0)

    def set_session_timeline(self, session: VoiceSession, highlighted_id: str = "") -> None:
        self.transcript.setTextFormat(Qt.TextFormat.RichText)
        self.transcript.setText(
            render_anchored_transcript(
                session.transcript,
                session.references,
                highlighted_id=highlighted_id,
            )
            or tr("Listening for speech...")
        )
        self.set_references(session.references)
        self._scroll_transcript_to_latest()

    def set_references(self, references: list) -> None:
        if not references:
            self.references.setText(tr("References: none"))
            return
        start = max(0, len(references) - 3)
        labels = "  ".join(
            f"[{index}] {ref.name}"
            for index, ref in enumerate(references[start:], start=start + 1)
        )
        prefix = f"{tr('References')} ({len(references)}): "
        self.references.setText(prefix + labels)

    def show_capture(self, reference, sensitive: bool = False) -> None:
        label = tr("Sensitive content detected") if sensitive else f"{tr('Captured')} {reference.id}"
        self.status.setText(label)
        self._apply_style("Error" if sensitive else "Listening")
        self.show_without_focus()
        QTimer.singleShot(1300, self._restore_capture_status)

    def _restore_capture_status(self) -> None:
        if not self.isVisible():
            return
        state = self._state if self._state in {"Listening", "Paused"} else "Listening"
        self.status.setText(tr(state))
        self._apply_style(state)

    def show_completion(
        self,
        prompt: str,
        reference_count: int,
        clipboard_state: str,
        *,
        restore_original: bool = False,
    ) -> None:
        self._state = "PromptReady"
        titles = {
            "copied": "Completed · Copied",
            "not_requested": "Completed · Not copied",
            "failed": "Completed · Copy failed",
        }
        title = titles.get(clipboard_state, "Completed")
        if clipboard_state == "copied" and restore_original:
            title = "Completed · Copied, original will be restored"
        self.status.setText(tr(title))
        self._apply_style("Error" if clipboard_state == "failed" else "PromptReady")
        self.transcript.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript.setText(prompt.strip())
        self._scroll_transcript_to_top()
        footer = tr("{count} references · Click to edit").format(count=reference_count)
        self.references.setText(footer)
        self.level.hide()
        self._hide_timer.stop()
        self.show_without_focus()
        self._hide_timer.start(int(self.config.get("completion_overlay_duration_ms", 4000)))

    def show_compose_failure(self, detail: str, reference_count: int) -> None:
        self._state = "Error"
        self.status.setText(tr("Generation failed"))
        self._apply_style("Error")
        self.transcript.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript.setText(tr(detail))
        self.references.setText(
            tr("Transcript and {count} references were preserved").format(count=reference_count)
        )
        self.level.hide()
        self._hide_timer.stop()
        self.show_without_focus()
        self._hide_timer.start(int(self.config.get("completion_overlay_duration_ms", 4000)))

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
            self._pointer_press(event)
            event.accept()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._pointer_press(event)
            return True
        if event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
            self._pointer_move(event)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._pointer_release()
            return True
        return super().eventFilter(watched, event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._pointer_move(event)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pointer_release()
            event.accept()

    def _pointer_press(self, event: QMouseEvent) -> None:
        position = event.globalPosition().toPoint()
        self._press_position = position
        self._drag_origin = position - self.frameGeometry().topLeft()
        self._dragging = False

    def _pointer_move(self, event: QMouseEvent) -> None:
        if self._drag_origin is None or self._press_position is None:
            return
        position = event.globalPosition().toPoint()
        if not self._dragging:
            distance = (position - self._press_position).manhattanLength()
            self._dragging = distance >= QApplication.startDragDistance()
        if self._dragging:
            self.move(position - self._drag_origin)
            self._pinned_position = True

    def _pointer_release(self) -> None:
        clicked = self._drag_origin is not None and not self._dragging
        self._drag_origin = None
        self._press_position = None
        self._dragging = False
        if clicked and self._state == "PromptReady":
            self._hide_timer.stop()
            self.hide()
            self.edit_requested.emit()
