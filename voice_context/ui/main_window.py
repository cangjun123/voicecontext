from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from voice_context.core.session import SessionState, VoiceSession
from .i18n import tr, translate_widget_tree
from .reference_widget import ReferenceEditDialog, ReferenceWidget


class MainWindow(QMainWindow):
    start_requested = Signal()
    finish_requested = Signal()
    pause_requested = Signal()
    cancel_requested = Signal()
    settings_requested = Signal()
    copy_requested = Signal(str)
    paste_requested = Signal(str)
    regenerate_requested = Signal()
    export_requested = Signal(str)
    reference_delete_requested = Signal(str)
    reference_update_requested = Signal(str, str, str)
    history_selected = Signal(str)
    history_delete_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VoiceContext")
        self.resize(1120, 760)
        self.setMinimumSize(900, 620)
        self._force_close = False
        self._session: VoiceSession | None = None
        self._history_ids: list[str] = []

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 20, 14, 16)
        title = QLabel("VoiceContext")
        title.setObjectName("appTitle")
        subtitle = QLabel("Prompt composer")
        subtitle.setObjectName("muted")
        side_layout.addWidget(title)
        side_layout.addWidget(subtitle)
        side_layout.addSpacing(24)
        self.workspace_nav = self._nav_button("Workspace", 0)
        self.history_nav = self._nav_button("History", 1)
        self.workspace_nav.setChecked(True)
        side_layout.addWidget(self.workspace_nav)
        side_layout.addWidget(self.history_nav)
        side_layout.addStretch()
        settings = QPushButton("Settings")
        settings.setObjectName("nav")
        settings.clicked.connect(self.settings_requested)
        side_layout.addWidget(settings)
        outer.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 18, 22, 20)
        content_layout.setSpacing(14)
        content_layout.addLayout(self._build_header())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_workspace())
        self.pages.addWidget(self._build_history())
        content_layout.addWidget(self.pages, 1)
        outer.addWidget(content, 1)
        translate_widget_tree(self)

    def _nav_button(self, text: str, index: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("nav")
        button.setCheckable(True)
        button.clicked.connect(lambda: self._show_page(index))
        return button

    def _show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.workspace_nav.setChecked(index == 0)
        self.history_nav.setChecked(index == 1)
        self.heading.setText(tr("Workspace" if index == 0 else "History"))
        if index == 1:
            self.context_label.setText(tr("Browse saved prompts and transcripts"))
        else:
            self.set_state(self._session.state if self._session else SessionState.IDLE)

    def show_history_page(self) -> None:
        self._show_page(1)
        self.show_normal()

    def _build_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        heading_box = QVBoxLayout()
        self.heading = QLabel("Workspace")
        self.heading.setStyleSheet("font-size: 20px; font-weight: 650;")
        self.context_label = QLabel("Ready for a voice session")
        self.context_label.setObjectName("muted")
        self.context_label.setWordWrap(True)
        heading_box.addWidget(self.heading)
        heading_box.addWidget(self.context_label)
        layout.addLayout(heading_box)
        layout.addStretch()
        self.status_badge = QLabel("Idle")
        self.status_badge.setObjectName("statusBadge")
        layout.addWidget(self.status_badge)
        self.start_button = QPushButton("Start")
        self.start_button.setObjectName("primary")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.clicked.connect(self.start_requested)
        layout.addWidget(self.start_button)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_button.clicked.connect(self.pause_requested)
        self.pause_button.setVisible(False)
        layout.addWidget(self.pause_button)
        self.stop_button = QPushButton("Compose")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.finish_requested)
        self.stop_button.setVisible(False)
        layout.addWidget(self.stop_button)
        self.cancel_button = QPushButton()
        self.cancel_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        self.cancel_button.setToolTip("Cancel current session")
        self.cancel_button.setFixedSize(34, 34)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.setVisible(False)
        layout.addWidget(self.cancel_button)
        return layout

    def _section_header(self, title: str, detail: str = "") -> QVBoxLayout:
        layout = QVBoxLayout()
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        if detail:
            info = QLabel(detail)
            info.setObjectName("muted")
            info.setWordWrap(True)
            layout.addWidget(info)
        return layout

    def _build_workspace(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addLayout(self._section_header("Live transcript", "Final and partial speech recognition output"))
        self.transcript_edit = QPlainTextEdit()
        self.transcript_edit.setReadOnly(True)
        self.transcript_edit.setPlaceholderText("Speech appears here while listening.")
        left_layout.addWidget(self.transcript_edit, 2)
        refs_header = QHBoxLayout()
        refs_title = QLabel("References")
        refs_title.setObjectName("sectionTitle")
        self.ref_count = QLabel("0 captured")
        self.ref_count.setObjectName("muted")
        refs_header.addWidget(refs_title)
        refs_header.addStretch()
        refs_header.addWidget(self.ref_count)
        left_layout.addLayout(refs_header)
        self.ref_scroll = QScrollArea()
        self.ref_scroll.setWidgetResizable(True)
        self.ref_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.ref_container = QWidget()
        self.ref_layout = QVBoxLayout(self.ref_container)
        self.ref_layout.setContentsMargins(0, 0, 4, 0)
        self.ref_layout.setSpacing(6)
        self.ref_empty = QLabel("Copy code, errors, paths, or URLs during a session.")
        self.ref_empty.setObjectName("muted")
        self.ref_empty.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.ref_layout.addWidget(self.ref_empty)
        self.ref_layout.addStretch()
        self.ref_scroll.setWidget(self.ref_container)
        left_layout.addWidget(self.ref_scroll, 3)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.addLayout(self._section_header("Prompt preview", "Editable before copying or pasting"))
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("The composed prompt appears here.")
        self.prompt_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        font = self.prompt_edit.font()
        font.setFamily("Cascadia Mono")
        font.setPointSize(10)
        self.prompt_edit.setFont(font)
        right_layout.addWidget(self.prompt_edit, 1)
        actions = QHBoxLayout()
        self.copy_button = QPushButton("Save and copy")
        self.copy_button.setObjectName("primary")
        self.copy_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.copy_button.clicked.connect(lambda: self.copy_requested.emit(self.prompt_edit.toPlainText()))
        self.paste_button = QPushButton("Paste")
        self.paste_button.clicked.connect(lambda: self.paste_requested.emit(self.prompt_edit.toPlainText()))
        self.regenerate_button = QPushButton("Regenerate")
        self.regenerate_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.regenerate_button.clicked.connect(self.regenerate_requested)
        export = QPushButton()
        export.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        export.setToolTip("Export prompt as Markdown")
        export.setFixedSize(34, 34)
        export.clicked.connect(lambda: self.export_requested.emit(self.prompt_edit.toPlainText()))
        actions.addWidget(self.copy_button)
        actions.addWidget(self.paste_button)
        actions.addWidget(self.regenerate_button)
        actions.addStretch()
        actions.addWidget(export)
        right_layout.addLayout(actions)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([510, 520])
        layout.addWidget(splitter, 1)
        return page

    def _build_history(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._section_header("Session history", "Saved prompts and their source transcripts"))
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Time", "Transcript", "References", "Source"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.itemSelectionChanged.connect(self._history_changed)
        splitter.addWidget(self.history_table)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 8, 0, 0)
        self.history_prompt = QPlainTextEdit()
        self.history_prompt.setReadOnly(True)
        detail_layout.addWidget(self.history_prompt)
        history_actions = QHBoxLayout()
        use_button = QPushButton("Open in workspace")
        use_button.clicked.connect(self._open_history_in_workspace)
        copy_button = QPushButton("Copy")
        copy_button.clicked.connect(lambda: self.copy_requested.emit(self.history_prompt.toPlainText()))
        delete_button = QPushButton("Delete")
        delete_button.setObjectName("danger")
        delete_button.clicked.connect(self._delete_history)
        history_actions.addWidget(use_button)
        history_actions.addWidget(copy_button)
        history_actions.addStretch()
        history_actions.addWidget(delete_button)
        detail_layout.addLayout(history_actions)
        splitter.addWidget(detail)
        splitter.setSizes([340, 240])
        layout.addWidget(splitter, 1)
        return page

    def set_session(self, session: VoiceSession) -> None:
        self._session = session
        self.transcript_edit.setPlainText(session.transcript)
        self.prompt_edit.setPlainText(session.final_prompt)
        self._rebuild_references(session)
        self.set_state(session.state)

    def update_transcript(self, text: str) -> None:
        self.transcript_edit.setPlainText(text)
        scrollbar = self.transcript_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_prompt(self, text: str) -> None:
        self.prompt_edit.setPlainText(text)

    def set_composer_status(self, metadata: dict, warning: str = "") -> None:
        if metadata.get("used"):
            status_template = (
                "Prompt ready via streaming LLM: {model} ({latency} ms)"
                if metadata.get("streamed")
                else "Prompt ready via LLM: {model} ({latency} ms)"
            )
            template = tr(status_template)
            status = template.format(
                model=metadata.get("response_model")
                or metadata.get("model")
                or metadata.get("provider")
                or "LLM",
                latency=metadata.get("latency_ms", 0),
            )
            usage = metadata.get("usage", {})
            if isinstance(usage, dict):
                input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
                output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    usage_text = tr("Tokens: {input} in / {output} out").format(
                        input=input_tokens,
                        output=output_tokens,
                    )
                    status += f" · {usage_text}"
            self.context_label.setText(status)
        elif metadata.get("requested"):
            self.context_label.setText(tr("LLM failed"))
        else:
            self.context_label.setText(tr("Prompt ready via rule composer"))
        self.context_label.setToolTip(warning)

    def set_completion_receipt(self, session: VoiceSession, warning: str = "") -> None:
        asr = session.asr_metadata
        asr_name = asr.get("model") or asr.get("provider") or "ASR"
        asr_status = (
            tr("succeeded")
            if asr.get("status") == "success"
            else tr(str(asr.get("status", "unknown")))
        )
        parts = [f"ASR {asr_name}: {asr_status}"]
        parts.append(tr("{count} references").format(count=len(session.references)))

        composer = session.composer_metadata
        if composer.get("used"):
            model = (
                composer.get("response_model")
                or composer.get("model")
                or composer.get("provider")
                or "LLM"
            )
            detail = f"LLM {model}"
            latency = composer.get("latency_ms")
            if isinstance(latency, int):
                detail += f" {latency} ms"
            usage = composer.get("usage", {})
            if isinstance(usage, dict):
                input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
                output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    detail += f" {input_tokens}->{output_tokens} token"
            parts.append(detail)
        elif composer.get("requested"):
            parts.append(tr("LLM failed"))
        else:
            parts.append(tr("Rule composer"))

        output_status = str(session.output_metadata.get("status", "pending"))
        output_labels = {
            "copied": "Clipboard verified",
            "failed": "Clipboard copy failed",
            "not_requested": "Clipboard not requested",
            "skipped": "Clipboard skipped",
            "pending": "Clipboard pending",
        }
        parts.append(tr(output_labels.get(output_status, output_status)))
        self.context_label.setText(" | ".join(parts))
        tooltip = "\n".join(
            filter(None, (warning, session.error, session.output_metadata.get("error", "")))
        )
        self.context_label.setToolTip(tooltip)

    def focus_prompt_editor(self) -> None:
        self._show_page(0)
        self.show_normal()
        self.prompt_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        cursor = self.prompt_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.prompt_edit.setTextCursor(cursor)

    def update_references(self, session: VoiceSession) -> None:
        self._session = session
        self._rebuild_references(session)

    def _rebuild_references(self, session: VoiceSession) -> None:
        while self.ref_layout.count():
            item = self.ref_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        refs = session.references
        self.ref_count.setText(f"{len(refs)} {tr('captured')}")
        if not refs:
            self.ref_empty = QLabel(tr("Copy code, errors, paths, or URLs during a session."))
            self.ref_empty.setObjectName("muted")
            self.ref_layout.addWidget(self.ref_empty)
        for ref in refs:
            widget = ReferenceWidget(ref)
            widget.edit_requested.connect(self._edit_reference)
            widget.delete_requested.connect(self.reference_delete_requested)
            self.ref_layout.addWidget(widget)
        self.ref_layout.addStretch()

    def _edit_reference(self, reference_id: str) -> None:
        if not self._session:
            return
        reference = next((item for item in self._session.references if item.id == reference_id), None)
        if not reference:
            return
        dialog = ReferenceEditDialog(reference, self)
        if dialog.exec():
            self.reference_update_requested.emit(
                reference_id, dialog.name_edit.text(), str(dialog.type_combo.currentData())
            )

    def set_state(self, state: SessionState) -> None:
        self.status_badge.setText(tr(state.value))
        active = state in {SessionState.LISTENING, SessionState.PAUSED}
        self.start_button.setVisible(not active and state != SessionState.COMPOSING)
        self.stop_button.setVisible(active)
        self.pause_button.setVisible(active)
        self.cancel_button.setVisible(active or state == SessionState.COMPOSING)
        self.pause_button.setText(tr("Resume" if state == SessionState.PAUSED else "Pause"))
        self.pause_button.setIcon(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_MediaPlay
                if state == SessionState.PAUSED
                else QStyle.StandardPixmap.SP_MediaPause
            )
        )
        ready = state == SessionState.PROMPT_READY
        self.copy_button.setEnabled(ready)
        self.paste_button.setEnabled(ready)
        self.regenerate_button.setEnabled(ready)
        messages = {
            SessionState.IDLE: "Ready for a voice session",
            SessionState.LISTENING: "Listening and watching the clipboard",
            SessionState.PAUSED: "Recording paused; clipboard capture remains active",
            SessionState.COMPOSING: "Transcribing and composing the final prompt",
            SessionState.PROMPT_READY: "Prompt is ready",
            SessionState.ERROR: "The session encountered an error",
        }
        self.context_label.setText(tr(messages.get(state, state.value)))

    def set_history(self, rows: list[dict]) -> None:
        self._history_ids = [str(row["id"]) for row in rows]
        self.history_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            created = datetime.fromtimestamp(row["created_at_ms"] / 1000).strftime("%Y-%m-%d %H:%M")
            values = [created, row.get("transcript", ""), str(row.get("reference_count", 0)), row.get("source_app", "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.history_table.setItem(index, column, item)
        if rows:
            self.history_table.selectRow(0)
        else:
            self.history_prompt.clear()

    def show_history_prompt(self, prompt: str) -> None:
        self.history_prompt.setPlainText(prompt)

    def _selected_history_id(self) -> str:
        row = self.history_table.currentRow()
        return self._history_ids[row] if 0 <= row < len(self._history_ids) else ""

    def _history_changed(self) -> None:
        session_id = self._selected_history_id()
        if session_id:
            self.history_selected.emit(session_id)

    def _open_history_in_workspace(self) -> None:
        text = self.history_prompt.toPlainText()
        if text:
            self.prompt_edit.setPlainText(text)
            self._show_page(0)

    def _delete_history(self) -> None:
        session_id = self._selected_history_id()
        if not session_id:
            return
        answer = QMessageBox.question(self, "Delete session", "Delete this session from local history?")
        if answer == QMessageBox.StandardButton.Yes:
            self.history_delete_requested.emit(session_id)

    def show_normal(self) -> None:
        self.show()
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_close:
            event.accept()
        else:
            event.ignore()
            self.hide()

    def shutdown(self) -> None:
        self._force_close = True
        self.close()
