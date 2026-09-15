from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
)

from .i18n import tr


class ReferenceEditDialog(QDialog):
    def __init__(self, reference, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{tr('Edit')} {reference.id}")
        self.resize(620, 430)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(reference.name)
        self.type_combo = QComboBox()
        for key, label in (
            ("file", "File"), ("code", "Code"), ("error", "Error"),
            ("log", "Log"), ("url", "URL"), ("text", "Text"),
        ):
            self.type_combo.addItem(tr(label), key)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(reference.content_type)))
        form.addRow(tr("Label"), self.name_edit)
        form.addRow(tr("Type"), self.type_combo)
        layout.addLayout(form)
        content = QPlainTextEdit(reference.content)
        content.setReadOnly(True)
        content.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(content, 1)
        source = " · ".join(filter(None, (reference.source_process, reference.source_window))) or "Unknown source"
        source_label = QLabel(source)
        source_label.setObjectName("muted")
        source_label.setWordWrap(True)
        layout.addWidget(source_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("Save"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))


class ReferenceWidget(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, reference, parent=None) -> None:
        super().__init__(parent)
        self.reference = reference
        self.setObjectName("referenceChip")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 8)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        title = QLabel(f"[{reference.id}]  {reference.name}")
        title.setStyleSheet("font-weight: 600;")
        preview = QLabel(reference.preview)
        preview.setObjectName("muted")
        preview.setWordWrap(True)
        type_label = QLabel(reference.content_type.upper())
        type_label.setStyleSheet("font-size: 10px; color: #6e777f;")
        text_layout.addWidget(title)
        text_layout.addWidget(preview)
        text_layout.addWidget(type_label)
        layout.addLayout(text_layout, 1)
        edit = QPushButton()
        edit.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        edit.setToolTip(tr("View or edit reference"))
        edit.setFixedSize(30, 30)
        edit.clicked.connect(lambda: self.edit_requested.emit(reference.id))
        delete = QPushButton()
        delete.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        delete.setToolTip(tr("Delete reference"))
        delete.setFixedSize(30, 30)
        delete.clicked.connect(self._confirm_delete)
        layout.addWidget(edit)
        layout.addWidget(delete)

    def _confirm_delete(self) -> None:
        answer = QMessageBox.question(
            self,
            tr("Delete reference"),
            tr("Delete this reference? This action cannot be undone."),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_requested.emit(self.reference.id)
