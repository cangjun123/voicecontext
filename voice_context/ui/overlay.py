from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QKeyEvent, QMouseEvent, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from voice_context.core.anchors import render_anchored_transcript
from voice_context.core.session import VoiceSession

from .i18n import tr, translate_widget_tree


class _InlinePromptEdit(QPlainTextEdit):
    commit_requested = Signal()
    cancel_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        modifiers = event.modifiers()
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and (
            modifiers & Qt.KeyboardModifier.ControlModifier
        ):
            self.commit_requested.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.cancel_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class OverlayWindow(QWidget):
    edit_requested = Signal()
    inline_save_requested = Signal(str)
    workspace_requested = Signal(str)
    inline_cancelled = Signal()
    dismiss_requested = Signal()

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
        self._hovering = False
        self._editing = False
        self._retryable_error = False
        self._completed_prompt = ""
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_hide_timer_timeout)
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
        self.close_button = QPushButton("×")
        self.close_button.setObjectName("overlayClose")
        self.close_button.setFixedSize(20, 20)
        self.close_button.setToolTip(tr("Dismiss"))
        self.close_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.close_button.clicked.connect(self._dismiss)
        header.addWidget(self.close_button)
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
        self.transcript_scroll.setMinimumHeight(40)
        self.transcript_scroll.setMaximumHeight(140)
        self.transcript_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.transcript_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # QAbstractScrollArea paints through a separate viewport. Making only the
        # outer QScrollArea transparent leaves that viewport on the system's light
        # base color, which renders the overlay's near-white transcript invisible.
        self.transcript_scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.transcript_scroll.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.transcript_scroll.viewport().setAutoFillBackground(False)
        self.transcript_scroll.viewport().setStyleSheet("background: transparent;")
        self.transcript.setStyleSheet("background: transparent;")
        self.transcript_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        layout.addWidget(self.transcript_scroll)
        self.editor = _InlinePromptEdit()
        self.editor.setObjectName("overlayEditor")
        self.editor.setMinimumHeight(96)
        self.editor.setMaximumHeight(160)
        self.editor.setVisible(False)
        self.editor.commit_requested.connect(self._commit_inline_edit)
        self.editor.cancel_requested.connect(self._cancel_inline_edit)
        layout.addWidget(self.editor)
        self.references = QLabel("")
        self.references.setWordWrap(True)
        self.references.setStyleSheet("color: #aeb5bd; font-size: 11px;")
        layout.addWidget(self.references)
        self.edit_actions = QWidget()
        edit_actions_layout = QHBoxLayout(self.edit_actions)
        edit_actions_layout.setContentsMargins(0, 2, 0, 0)
        edit_actions_layout.setSpacing(6)
        self.inline_save_button = QPushButton("Save and copy")
        self.inline_save_button.setObjectName("overlayPrimary")
        self.inline_save_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.inline_save_button.clicked.connect(self._commit_inline_edit)
        self.inline_workspace_button = QPushButton("Open in workspace")
        self.inline_workspace_button.setObjectName("overlaySecondary")
        self.inline_workspace_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.inline_workspace_button.clicked.connect(self._open_in_workspace)
        self.inline_cancel_button = QPushButton("Cancel")
        self.inline_cancel_button.setObjectName("overlaySecondary")
        self.inline_cancel_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.inline_cancel_button.clicked.connect(self._cancel_inline_edit)
        edit_actions_layout.addWidget(self.inline_save_button)
        edit_actions_layout.addWidget(self.inline_workspace_button)
        edit_actions_layout.addStretch()
        edit_actions_layout.addWidget(self.inline_cancel_button)
        self.edit_actions.setVisible(False)
        layout.addWidget(self.edit_actions)
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
        self.setMouseTracking(True)
        self.frame.setMouseTracking(True)

    def _apply_style(self, state: str) -> None:
        color = self.COLORS.get(state, self.COLORS["Idle"])
        self.dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.frame.setStyleSheet(
            "QFrame#overlayFrame { background: rgba(30, 33, 37, 242); "
            "border: 1px solid rgba(255,255,255,35); border-radius: 7px; } "
            "QLabel { color: #f2f3f4; } QProgressBar { background: #45494e; border: none; } "
            f"QProgressBar::chunk {{ background: {color}; }} "
            "QPushButton#overlayClose { background: transparent; color: rgba(255,255,255,120); "
            "border: none; border-radius: 10px; font-size: 15px; font-weight: 400; padding: 0px; } "
            "QPushButton#overlayClose:hover { background: rgba(255,255,255,40); color: #ffffff; } "
            "QPushButton#overlayClose:pressed { background: rgba(255,255,255,60); } "
            "QPlainTextEdit#overlayEditor { background: rgba(18, 20, 23, 230); color: #f2f3f4; "
            "border: 1px solid rgba(40, 161, 117, 180); border-radius: 5px; padding: 5px 6px; font-size: 12px; } "
            "QPushButton#overlayPrimary { background: #28a175; color: #ffffff; border: none; "
            "border-radius: 4px; padding: 4px 10px; font-size: 11px; font-weight: 600; } "
            "QPushButton#overlayPrimary:hover { background: #2fb886; } "
            "QPushButton#overlaySecondary { background: rgba(255,255,255,22); color: #e2e6ea; "
            "border: 1px solid rgba(255,255,255,35); border-radius: 4px; padding: 4px 8px; font-size: 11px; } "
            "QPushButton#overlaySecondary:hover { background: rgba(255,255,255,38); color: #ffffff; }"
        )
        self.frame.setCursor(
            QCursor(
                Qt.CursorShape.PointingHandCursor
                if (state == "PromptReady" and not self._editing)
                or (state == "Error" and self._retryable_error)
                else Qt.CursorShape.ArrowCursor
            )
        )

    def _dismiss(self) -> None:
        was_editing = self._editing
        self.finish_inline_edit()
        self._hide_timer.stop()
        self.hide()
        if was_editing:
            self.inline_cancelled.emit()
        self.dismiss_requested.emit()

    def _restore_non_activating_mode(self) -> None:
        if not self.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating):
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        if not (self.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus):
            self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)

    def _leave_inline_edit_ui(self) -> None:
        if not self._editing and not self.editor.isVisible():
            return
        self._editing = False
        self.editor.setVisible(False)
        self.edit_actions.setVisible(False)
        self.transcript_scroll.setVisible(True)
        self.references.setVisible(True)
        self.setMaximumHeight(250)
        self._restore_non_activating_mode()

    def start_inline_edit(self) -> None:
        if self._state != "PromptReady":
            return
        self._hide_timer.stop()
        self._editing = True
        self.setMaximumHeight(330)
        self.status.setText(tr("Editing · Ctrl+Enter to save & copy"))
        self._apply_style("PromptReady")
        self.transcript_scroll.setVisible(False)
        self.references.setVisible(False)
        self.editor.setPlainText(self._completed_prompt)
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)
        self.editor.setVisible(True)
        self.edit_actions.setVisible(True)
        self.edit_requested.emit()
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, False)
        if not self._pinned_position:
            self.reposition()
        self.show()
        self.raise_()
        self.activateWindow()
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def finish_inline_edit(self) -> None:
        self._leave_inline_edit_ui()
        self._hide_timer.stop()
        self.hide()

    def _commit_inline_edit(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            return
        self._completed_prompt = text
        self.finish_inline_edit()
        self.inline_save_requested.emit(text)

    def _cancel_inline_edit(self) -> None:
        self.finish_inline_edit()
        self.inline_cancelled.emit()

    def _open_in_workspace(self) -> None:
        text = self.editor.toPlainText()
        self.finish_inline_edit()
        self.workspace_requested.emit(text)

    def enterEvent(self, event) -> None:
        self._hovering = True
        if self._hide_timer.isActive():
            self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovering = False
        if not self._editing and self._state in {"PromptReady", "Error", "Idle"}:
            self._hide_timer.start(int(self.config.get("overlay_duration_ms", 2600)))
        super().leaveEvent(event)

    def _on_hide_timer_timeout(self) -> None:
        if not self._hovering and not self._editing:
            self.hide()

    def retranslate_ui(self) -> None:
        translate_widget_tree(self)
        self.close_button.setToolTip(tr("Dismiss"))

    def set_state(self, state: str, detail: str = "") -> None:
        self._leave_inline_edit_ui()
        self._retryable_error = False
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
        if state in {"PromptReady", "Error", "Idle"} and not self._hovering:
            self._hide_timer.start(int(self.config.get("overlay_duration_ms", 2600)))

    def set_composing_stream(self, text: str) -> None:
        value = text.strip()
        if not value or self._state != "Composing":
            return
        if self.transcript.text() == value:
            return
        self.transcript.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript.setText(value)
        self._scroll_transcript_to_latest()

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
        self._leave_inline_edit_ui()
        self._retryable_error = False
        self._state = "PromptReady"
        self._completed_prompt = prompt.strip()
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
        self.transcript.setText(self._completed_prompt)
        self._scroll_transcript_to_top()
        footer = tr("{count} references · Double-click to edit · × to dismiss").format(count=reference_count)
        self.references.setText(footer)
        self.level.hide()
        self._hide_timer.stop()
        self.show_without_focus()
        self._hide_timer.start(int(self.config.get("completion_overlay_duration_ms", 4000)))

    def show_compose_failure(
        self, detail: str, reference_count: int, retryable: bool = True
    ) -> None:
        self._leave_inline_edit_ui()
        self._retryable_error = retryable
        self._state = "Error"
        self.status.setText(tr("Generation failed"))
        self._apply_style("Error")
        self.transcript.setTextFormat(Qt.TextFormat.PlainText)
        self.transcript.setText(tr(detail))
        footer_template = (
            "Transcript and {count} references were preserved · Double-click to retry"
            if retryable
            else "Transcript and {count} references were preserved"
        )
        self.references.setText(tr(footer_template).format(count=reference_count))
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
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
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

    def _is_interactive_child(self, watched) -> bool:
        if watched in {
            self.close_button,
            self.editor,
            self.inline_save_button,
            self.inline_workspace_button,
            self.inline_cancel_button,
        }:
            return True
        return isinstance(watched, QWidget) and self.editor.isAncestorOf(watched)

    def eventFilter(self, watched, event) -> bool:
        # Let interactive buttons and the inline editor handle their own mouse/key events.
        if self._is_interactive_child(watched):
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._pointer_press(event)
            return True
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self._handle_double_click()
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

    def _handle_double_click(self) -> None:
        if self._state == "PromptReady" and not self._editing:
            self.start_inline_edit()
        elif self._state == "Error" and self._retryable_error:
            self._hide_timer.stop()
            self.hide()
            self.workspace_requested.emit("")

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._handle_double_click()
            event.accept()

    def _pointer_release(self) -> None:
        clicked = self._drag_origin is not None and not self._dragging
        self._drag_origin = None
        self._press_position = None
        self._dragging = False
        if clicked and self._state == "PromptReady":
            # Single click now only pauses the hide timer; editing requires double-click.
            self._hide_timer.stop()
