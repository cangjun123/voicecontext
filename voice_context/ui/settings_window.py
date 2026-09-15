from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voice_context.core.templates import validate_template
from voice_context.input.audio_capture import AudioCapture
from voice_context.input.hotkeys import HotkeyManager
from voice_context.storage.config_store import ConfigStore, DEFAULT_CONFIG
from voice_context.storage.profile import PROFILE_KEYS, ProfileError, read_settings_bundle, write_settings_bundle
from voice_context.storage.secrets import SecretStore
from .hotkey_capture import HotkeyCaptureEdit
from .i18n import tr, translate_widget_tree


class SettingsWindow(QDialog):
    saved = Signal()
    hotkeys_page_entered = Signal()
    hotkeys_page_exited = Signal()
    test_asr_requested = Signal(object, str, str)
    test_llm_requested = Signal(object, str)
    clear_history_requested = Signal()

    def __init__(self, config: ConfigStore, secrets: SecretStore, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.secrets = secrets
        self.values = copy.deepcopy(config.data)
        self.bindings: list[tuple[str, QWidget, Callable[[QWidget], Any]]] = []
        self.hotkey_status: dict[str, QLabel] = {}
        self.hotkey_edits: dict[str, HotkeyCaptureEdit] = {}
        self._imported_secrets: dict[str, str] = {}
        self._current_page = -1
        self.setWindowTitle("VoiceContext Settings")
        self.resize(940, 720)
        self.setMinimumSize(820, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 12)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(200)
        self.nav.setFrameShape(QListWidget.Shape.NoFrame)
        self.nav.setSpacing(0)
        self.nav.setContentsMargins(8, 8, 8, 8)
        self.pages = QStackedWidget()
        self._builders = {
            "General": self._general_page,
            "Hotkeys": self._hotkeys_page,
            "ASR": self._asr_page,
            "LLM": self._llm_page,
            "Prompt": self._prompt_page,
            "Clipboard": self._clipboard_page,
            "Output": self._output_page,
            "Privacy": self._privacy_page,
            "Advanced": self._advanced_page,
        }
        for name, builder in self._builders.items():
            self.nav.addItem(QListWidgetItem(name))
            self.pages.addWidget(self._scroll_page(name, builder()))
        self.nav.currentRowChanged.connect(self._page_changed)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        footer = QHBoxLayout()
        footer.setContentsMargins(14, 0, 14, 0)
        reset = QPushButton("Restore defaults")
        reset.clicked.connect(self._restore_defaults)
        footer.addWidget(reset)
        export = QPushButton("Export settings...")
        export.clicked.connect(self._export_settings)
        footer.addWidget(export)
        importer = QPushButton("Import settings...")
        importer.clicked.connect(self._import_settings)
        footer.addWidget(importer)
        footer.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        root.addLayout(footer)
        translate_widget_tree(self)

    def _page_changed(self, index: int) -> None:
        previous = self._current_page
        if previous == 1 and index != 1:
            self.hotkeys_page_exited.emit()
        self._current_page = index
        self.pages.setCurrentIndex(index)
        if index == 1 and previous != 1:
            self.hotkeys_page_entered.emit()
            self.refresh_hotkey_validation()

    def refresh_hotkey_validation(self) -> None:
        for key, edit in self.hotkey_edits.items():
            self._validate_hotkey(key, edit.text())

    def _scroll_page(self, name: str, content: QWidget) -> QScrollArea:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(24, 20, 24, 24)
        title = QLabel(name)
        title.setStyleSheet("font-size: 20px; font-weight: 650;")
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(content)
        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(wrapper)
        return scroll

    def _form_page(self) -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(13)
        return page, form

    def _value(self, path: str) -> Any:
        current: Any = self.values
        for part in path.split("."):
            current = current.get(part, {}) if isinstance(current, dict) else None
        return current

    def _bind(self, path: str, widget: QWidget, getter: Callable[[QWidget], Any]) -> QWidget:
        self.bindings.append((path, widget, getter))
        return widget

    def _line(self, path: str, password: bool = False, placeholder: str = "") -> QLineEdit:
        widget = QLineEdit(str(self._value(path) or ""))
        widget.setPlaceholderText(placeholder)
        if password:
            widget.setEchoMode(QLineEdit.EchoMode.Password)
        return self._bind(path, widget, lambda item: item.text().strip())  # type: ignore[return-value]

    def _secret_line(self, section: str) -> QLineEdit:
        provider = str(self._value(f"{section}.provider"))
        widget = QLineEdit(self.secrets.get(SecretStore.key_for(section, provider)))
        widget.setEchoMode(QLineEdit.EchoMode.Password)
        widget.setPlaceholderText("Stored securely in Windows Credential Manager")
        return widget

    def _check(self, path: str) -> QCheckBox:
        widget = QCheckBox()
        widget.setChecked(bool(self._value(path)))
        return self._bind(path, widget, lambda item: item.isChecked())  # type: ignore[return-value]

    def _combo(self, path: str, choices: list[tuple[str, str]]) -> QComboBox:
        widget = QComboBox()
        for value, label in choices:
            widget.addItem(label, value)
        index = widget.findData(str(self._value(path)))
        widget.setCurrentIndex(index if index >= 0 else 0)
        return self._bind(path, widget, lambda item: item.currentData())  # type: ignore[return-value]

    def _spin(self, path: str, minimum: int, maximum: int, suffix: str = "") -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(int(self._value(path)))
        widget.setSuffix(suffix)
        return self._bind(path, widget, lambda item: item.value())  # type: ignore[return-value]

    def _double(self, path: str, minimum: float, maximum: float, step: float, decimals: int = 2) -> QDoubleSpinBox:
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setSingleStep(step)
        widget.setDecimals(decimals)
        widget.setValue(float(self._value(path)))
        return self._bind(path, widget, lambda item: item.value())  # type: ignore[return-value]

    @staticmethod
    def _note(text: str) -> QLabel:
        note = QLabel(text)
        note.setObjectName("muted")
        note.setWordWrap(True)
        return note

    def _general_page(self) -> QWidget:
        page, form = self._form_page()
        form.addRow("Start with Windows", self._check("general.startup"))
        form.addRow("Theme", self._combo("general.theme", [("system", "System"), ("light", "Light"), ("dark", "Dark")]))
        form.addRow("Interface language", self._combo("general.language", [("zh_CN", "简体中文"), ("en", "English")]))
        form.addRow("Default input mode", self._combo("general.default_mode", [("hold", "Hold to talk"), ("toggle", "Toggle")]))
        form.addRow("Overlay position", self._combo("general.overlay_position", [
            ("bottom_center", "Bottom center"), ("bottom_right", "Bottom right"),
            ("top_center", "Top center"), ("top_right", "Top right")]))
        form.addRow("Overlay opacity", self._double("general.overlay_opacity", 0.4, 1.0, 0.05))
        form.addRow("Feedback duration", self._spin("general.overlay_duration_ms", 500, 10000, " ms"))
        form.addRow(
            "Completion result duration",
            self._spin("general.completion_overlay_duration_ms", 1000, 15000, " ms"),
        )
        form.addRow("Keep history", self._spin("general.history_days", 0, 3650, " days"))
        form.addRow("", self._note("Set history retention to 0 to remove sessions after the current day."))
        return page

    def _hotkeys_page(self) -> QWidget:
        page, form = self._form_page()
        labels = {
            "hold_to_talk": "Hold to talk",
            "toggle_listening": "Start / finish and compose",
            "finish": "Finish and compose",
            "cancel": "Cancel session",
            "pause_resume": "Pause / resume",
            "undo_reference": "Delete last reference",
            "ignore_reference": "Ignore last capture",
            "toggle_overlay": "Show / hide overlay",
            "recopy_prompt": "Recopy last prompt",
        }
        for key, label in labels.items():
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            edit = HotkeyCaptureEdit(str(self._value(f"hotkeys.{key}") or ""))
            self._bind(f"hotkeys.{key}", edit, lambda item: item.text().strip())
            status = QLabel("Ready")
            status.setObjectName("muted")
            status.setFixedWidth(100)
            edit.textChanged.connect(lambda text, current=key: self._validate_hotkey(current, text))
            self.hotkey_edits[key] = edit
            self.hotkey_status[key] = status
            self._validate_hotkey(key, edit.text())
            layout.addWidget(edit, 1)
            layout.addWidget(status)
            form.addRow(label, row)
        form.addRow("", self._note("Click a field, then press the shortcut. Press Esc to cancel or Backspace/Delete to clear. Toggle listening and Finish may share one shortcut."))
        return page

    def _validate_hotkey(self, key: str, text: str) -> None:
        valid, message = HotkeyManager.availability(text)
        label = self.hotkey_status[key]
        if valid:
            label.setText(tr("Available"))
            label.setStyleSheet("color: #258263;")
        else:
            label.setText(tr("Conflict"))
            label.setStyleSheet("color: #c85a5a;")
            # Show detailed conflict message in tooltip and as status tip
            label.setToolTip(message)
            # Also check for internal duplicates
            duplicates = HotkeyManager.duplicate_conflicts(self.values.get("hotkeys", {}))
            if text and text in duplicates:
                label.setToolTip(tr("Conflicts with another VoiceContext shortcut"))

    def _asr_page(self) -> QWidget:
        page, form = self._form_page()
        self.asr_provider = self._combo("asr.provider", [
            ("aliyun_realtime", "Aliyun Model Studio realtime (WebSocket)"),
            ("openai_compatible", "OpenAI-compatible transcription API"),
            ("custom_api", "Custom ASR API"),
        ])
        form.addRow("Provider", self.asr_provider)
        form.addRow("", self._note("Voice audio is sent only to the configured remote ASR provider."))
        self.asr_model = self._line("asr.model")
        form.addRow("Model name", self.asr_model)
        self.asr_base_url = self._line("asr.base_url")
        form.addRow("API base URL", self.asr_base_url)
        self.asr_key = self._secret_line("asr")
        self.asr_provider.currentIndexChanged.connect(self._asr_provider_changed)
        form.addRow("API key", self.asr_key)
        form.addRow("Language", self._line("asr.language", placeholder="zh, en, or blank for auto"))
        form.addRow("Sample rate", self._spin("asr.sample_rate", 8000, 96000, " Hz"))
        self.asr_input_device = QComboBox()
        self.asr_input_device.setEditable(False)
        self._refresh_audio_devices()
        self._bind("asr.input_device", self.asr_input_device, lambda item: item.currentData() or "")
        form.addRow("Input device", self.asr_input_device)
        form.addRow("Voice activity detection", self._check("asr.vad"))
        form.addRow("Silence threshold", self._double("asr.silence_threshold", 0.001, 0.2, 0.001, 3))
        form.addRow("Silence duration", self._spin("asr.silence_ms", 100, 5000, " ms"))
        form.addRow("Show streaming partials", self._check("asr.streaming_partial"))
        form.addRow("Partial interval", self._double("asr.partial_interval_s", 0.8, 15, 0.5, 1))
        form.addRow("Restore punctuation", self._check("asr.punctuation"))
        form.addRow("Hotwords", self._line("asr.hotwords", placeholder="Comma-separated words"))
        form.addRow("Aliyun vocabulary ID", self._line("asr.phrase_id", placeholder="Optional vocabulary resource ID"))
        form.addRow("Semantic sentence detection", self._check("asr.semantic_punctuation"))
        form.addRow("Remove disfluencies", self._check("asr.disfluency_removal"))
        form.addRow("Request timeout", self._spin("asr.timeout_s", 5, 600, " s"))
        self.asr_headers = QPlainTextEdit(json.dumps(self._value("asr.custom_headers"), indent=2))
        self.asr_headers.setMaximumHeight(90)
        self.asr_headers.textChanged.connect(lambda: self._validate_json_field(self.asr_headers))
        form.addRow("Custom headers (JSON)", self.asr_headers)
        self.asr_test_button = QPushButton("Test with a WAV file")
        self.asr_test_button.clicked.connect(self._test_asr)
        form.addRow("", self.asr_test_button)
        return page

    def _validate_json_field(self, field: QPlainTextEdit) -> None:
        try:
            text = field.toPlainText().strip()
            if not text:
                field.setStyleSheet("")
                return
            json.loads(text)
            field.setStyleSheet("border: 1px solid #258263;")
        except json.JSONDecodeError:
            field.setStyleSheet("border: 1px solid #c85a5a;")

    def _refresh_audio_devices(self) -> None:
        current = str(self._value("asr.input_device") or "")
        self.asr_input_device.clear()
        self.asr_input_device.addItem(tr("Default (Windows)"), "")
        devices = AudioCapture.devices()
        for device in devices:
            name = device.get("name", "")
            if name:
                self.asr_input_device.addItem(name, name)
        if current:
            index = self.asr_input_device.findData(current)
            if index >= 0:
                self.asr_input_device.setCurrentIndex(index)
            else:
                # Device not found, add it as a custom entry to preserve config
                self.asr_input_device.addItem(f"{current} (not found)", current)
                self.asr_input_device.setCurrentIndex(self.asr_input_device.count() - 1)

    def _asr_provider_changed(self) -> None:
        self._load_provider_secret("asr", self.asr_provider, self.asr_key)
        provider = self.asr_provider.currentData()
        model = self.asr_model.text().strip()
        current_url = self.asr_base_url.text().strip()
        if provider == "aliyun_realtime":
            if model in {"", "whisper-1"}:
                self.asr_model.setText("fun-asr-realtime")
            if not current_url or current_url == "https://api.openai.com/v1":
                self.asr_base_url.setText("https://dashscope.aliyuncs.com/api/v1")
        elif provider == "openai_compatible":
            if model in {"", "fun-asr-realtime"}:
                self.asr_model.setText("whisper-1")
            if not current_url or current_url == "https://dashscope.aliyuncs.com/api/v1":
                self.asr_base_url.setText("https://api.openai.com/v1")

    def _llm_page(self) -> QWidget:
        page, form = self._form_page()
        form.addRow("Enable LLM composer", self._check("llm.enabled"))
        self.llm_provider = self._combo("llm.provider", [
            ("openai_compatible", "OpenAI-compatible API"), ("ollama", "Ollama"),
            ("lm_studio", "LM Studio"), ("custom_http", "Custom HTTP endpoint"),
            ("rules", "Disabled / rules only")])
        form.addRow("Provider", self.llm_provider)
        form.addRow("Base URL", self._line("llm.base_url"))
        self.llm_key = self._secret_line("llm")
        self.llm_provider.currentIndexChanged.connect(
            lambda: self._load_provider_secret("llm", self.llm_provider, self.llm_key)
        )
        form.addRow("API key", self.llm_key)
        form.addRow("Model", self._line("llm.model"))
        form.addRow("Temperature", self._double("llm.temperature", 0, 2, 0.1, 2))
        form.addRow("Max tokens", self._spin("llm.max_tokens", 64, 32768))
        form.addRow("Timeout", self._spin("llm.timeout_s", 5, 600, " s"))
        self.system_prompt = QPlainTextEdit(str(self._value("llm.system_prompt")))
        self.system_prompt.setMaximumHeight(130)
        form.addRow("System prompt", self.system_prompt)
        form.addRow("Resolve demonstratives", self._check("llm.resolve_references"))
        form.addRow("Polish spoken language", self._check("llm.polish"))
        form.addRow("Preserve Chinese output", self._check("llm.preserve_chinese"))
        self.llm_headers = QPlainTextEdit(json.dumps(self._value("llm.custom_headers"), indent=2))
        self.llm_headers.setMaximumHeight(90)
        self.llm_headers.textChanged.connect(lambda: self._validate_json_field(self.llm_headers))
        form.addRow("Custom headers (JSON)", self.llm_headers)
        self.llm_test_button = QPushButton("Test composer connection")
        self.llm_test_button.clicked.connect(self._test_llm)
        form.addRow("", self.llm_test_button)
        return page

    def _prompt_page(self) -> QWidget:
        page, form = self._form_page()
        self.target_combo = self._combo("prompt.target", [
            ("generic_markdown", "Generic Markdown"), ("codex_cli", "Codex CLI"),
            ("claude_code", "Claude Code"), ("cursor", "Cursor"),
            ("chatgpt", "ChatGPT"), ("custom", "Custom")])
        form.addRow("Target", self.target_combo)
        self.template_edit = QPlainTextEdit()
        self.template_edit.setMinimumHeight(300)
        font = self.template_edit.font()
        font.setFamily("Cascadia Mono")
        self.template_edit.setFont(font)
        self._template_target = str(self.target_combo.currentData())
        self.template_edit.setPlainText(str(self._value(f"prompt.templates.{self._template_target}")))
        self.target_combo.currentIndexChanged.connect(self._change_template)
        form.addRow("Template", self.template_edit)
        form.addRow("", self._note("Variables: {{task}}, {{references}}, {{transcript}}, {{timestamp}}, {{source_app}}"))
        return page

    def _change_template(self) -> None:
        self.values["prompt"]["templates"][self._template_target] = self.template_edit.toPlainText()
        self._template_target = str(self.target_combo.currentData())
        self.template_edit.setPlainText(str(self.values["prompt"]["templates"].get(self._template_target, "")))

    def _clipboard_page(self) -> QWidget:
        page, form = self._form_page()
        items = [
            ("capture_during_session", "Capture clipboard changes during a session"),
            ("capture_text", "Capture plain text"), ("capture_files", "Capture file paths"),
            ("capture_html", "Convert HTML to plain text"), ("capture_urls", "Capture URLs"),
            ("dedupe", "Deduplicate content"), ("show_toast", "Show capture feedback"),
            ("restore_original", "Preserve clipboard before output"),
            ("sensitive_warning", "Warn about sensitive content"),
        ]
        for key, label in items:
            form.addRow(label, self._check(f"clipboard.{key}"))
        form.addRow("Maximum capture length", self._spin("clipboard.max_chars", 100, 1000000, " chars"))
        return page

    def _output_page(self) -> QWidget:
        page, form = self._form_page()
        items = [
            ("copy_to_clipboard", "Copy final prompt to clipboard"),
            ("auto_paste", "Automatically paste into the source window"),
            ("auto_submit", "Press Enter after automatic paste"),
            ("confirm_before_paste", "Confirm before automatic paste"),
            ("save_history", "Save completed sessions"),
        ]
        for key, label in items:
            form.addRow(label, self._check(f"output.{key}"))
        form.addRow("Markdown export directory", self._line("output.export_directory", placeholder="Defaults to Documents\\VoiceContext"))
        return page

    def _privacy_page(self) -> QWidget:
        page, form = self._form_page()
        items = [
            ("local_logs", "Keep local session metadata"),
            ("save_reference_content", "Save reference contents in history"),
            ("auto_cleanup", "Automatically clean old history"),
            ("sensitive_detection", "Detect likely secrets before capture"),
            ("debug_logging", "Enable debug logs"),
        ]
        for key, label in items:
            form.addRow(label, self._check(f"privacy.{key}"))
        clear = QPushButton("Clear all session history")
        clear.setObjectName("danger")
        clear.clicked.connect(self._clear_history)
        form.addRow("Local data", clear)
        form.addRow("API key storage", self._note("API keys are stored by Windows Credential Manager and excluded from config.json and session history."))
        return page

    def _advanced_page(self) -> QWidget:
        page, form = self._form_page()
        form.addRow("Audio callback chunk", self._spin("advanced.audio_chunk_ms", 20, 1000, " ms"))
        form.addRow("Keep source audio", self._check("advanced.keep_audio"))
        form.addRow("Audio archive directory", self._line("advanced.audio_directory"))
        form.addRow("Log level", self._combo("advanced.log_level", [("DEBUG", "Debug"), ("INFO", "Info"), ("WARNING", "Warning"), ("ERROR", "Error")]))
        form.addRow("Start minimized to tray", self._check("advanced.start_minimized"))
        return page

    def _collect(self) -> tuple[bool, str]:
        for path, widget, getter in self.bindings:
            value = getter(widget)
            current = self.values
            parts = path.split(".")
            for part in parts[:-1]:
                current = current.setdefault(part, {})
            current[parts[-1]] = value
        self.values["prompt"]["templates"][self._template_target] = self.template_edit.toPlainText()
        self.values["llm"]["system_prompt"] = self.system_prompt.toPlainText().strip()
        try:
            self.values["asr"]["custom_headers"] = json.loads(self.asr_headers.toPlainText() or "{}")
            self.values["llm"]["custom_headers"] = json.loads(self.llm_headers.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            return False, f"Custom headers must be valid JSON: {exc}"
        if not isinstance(self.values["asr"]["custom_headers"], dict) or not isinstance(
            self.values["llm"]["custom_headers"], dict
        ):
            return False, "Custom headers must be JSON objects."
        hotkeys = [HotkeyManager.normalize(value) for value in self.values["hotkeys"].values() if value]
        duplicates = HotkeyManager.duplicate_conflicts(self.values["hotkeys"])
        if duplicates:
            return False, "Duplicate hotkeys: " + ", ".join(duplicates)
        for value in hotkeys:
            valid, message = HotkeyManager.availability(value)
            if not valid:
                return False, f"Invalid hotkey {value!r}: {message}"
        target = self.values["prompt"]["target"]
        valid, message = validate_template(self.values["prompt"]["templates"][target])
        if not valid:
            return False, message
        return True, ""

    def _save(self) -> None:
        valid, message = self._collect()
        if not valid:
            QMessageBox.warning(self, "Invalid settings", message)
            return
        self.config.data = copy.deepcopy(self.values)
        self.config.save()
        asr_provider = str(self.values["asr"]["provider"])
        llm_provider = str(self.values["llm"]["provider"])
        try:
            self.secrets.set(SecretStore.key_for("asr", asr_provider), self.asr_key.text().strip())
            self.secrets.set(SecretStore.key_for("llm", llm_provider), self.llm_key.text().strip())
            for name, value in self._imported_secrets.items():
                if value:
                    self.secrets.set(name, value)
        except RuntimeError as exc:
            QMessageBox.critical(self, "Credential storage failed", str(exc))
            return
        self.saved.emit()
        self.accept()

    def _load_provider_secret(self, section: str, combo: QComboBox, line_edit: QLineEdit) -> None:
        provider = str(combo.currentData())
        name = SecretStore.key_for(section, provider)
        line_edit.setText(self._imported_secrets.get(name) or self.secrets.get(name))

    def _export_settings(self) -> None:
        valid, message = self._collect()
        if not valid:
            QMessageBox.warning(self, "Invalid settings", message)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export settings",
            str(Path.home() / "voicecontext-settings.json"),
            "VoiceContext settings (*.json)",
        )
        if not path:
            return
        # Merge the keys typed in this dialog with any other provider keys already in
        # the credential store, so switching providers does not lose them.
        secrets = {name: value for name in PROFILE_KEYS if (value := self.secrets.get(name))}
        secrets[SecretStore.key_for("asr", str(self.values["asr"]["provider"]))] = (
            self.asr_key.text().strip()
        )
        secrets[SecretStore.key_for("llm", str(self.values["llm"]["provider"]))] = (
            self.llm_key.text().strip()
        )
        try:
            write_settings_bundle(Path(path), copy.deepcopy(self.values), secrets)
        except (OSError, ProfileError) as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Settings exported",
            "Settings and API keys were saved. The file contains secrets in plain text; store it safely.",
        )

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import settings",
            str(Path.home()),
            "VoiceContext settings (*.json);;All files (*.*)",
        )
        if not path:
            return
        try:
            settings, secrets = read_settings_bundle(Path(path))
        except ProfileError as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Import settings",
            "Replace every setting in this window with the imported file? "
            "Nothing is saved until you press Save.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.values = copy.deepcopy(settings)
        self._rebuild_pages()
        self._imported_secrets = secrets
        asr_key = secrets.get(SecretStore.key_for("asr", str(self.values["asr"]["provider"])), "")
        llm_key = secrets.get(SecretStore.key_for("llm", str(self.values["llm"]["provider"])), "")
        self.asr_key.setText(asr_key)
        self.llm_key.setText(llm_key)
        QMessageBox.information(
            self,
            "Settings imported",
            "Review the pages, then press Save to apply (API keys are restored on save).",
        )

    def _rebuild_pages(self) -> None:
        self.bindings.clear()
        self.hotkey_edits.clear()
        self.hotkey_status.clear()
        for index in range(self.pages.count()):
            self.pages.removeWidget(self.pages.widget(0))
        self.nav.clear()
        self._current_page = -1
        for name, builder in self._builders.items():
            self.nav.addItem(QListWidgetItem(name))
            self.pages.addWidget(self._scroll_page(name, builder()))
        self.nav.setCurrentRow(0)
        translate_widget_tree(self)

    def _restore_defaults(self) -> None:
        answer = QMessageBox.question(self, "Restore defaults", "Reset all settings except stored API keys?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config.data = copy.deepcopy(DEFAULT_CONFIG)
        self.config.save()
        self.saved.emit()
        self.accept()

    def _clear_history(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Clear history",
            "Permanently remove all locally saved sessions?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.clear_history_requested.emit()
            QMessageBox.information(self, "History", "Session history was cleared.")

    def _test_asr(self) -> None:
        valid, message = self._collect()
        if not valid:
            QMessageBox.warning(self, "Invalid settings", message)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose test audio", str(Path.home()), "WAV audio (*.wav)"
        )
        if path:
            self.asr_test_button.setEnabled(False)
            self.asr_test_button.setText(tr("Testing..."))
            self.test_asr_requested.emit(copy.deepcopy(self.values["asr"]), self.asr_key.text().strip(), path)

    def _test_llm(self) -> None:
        valid, message = self._collect()
        if not valid:
            QMessageBox.warning(self, "Invalid settings", message)
            return
        self.llm_test_button.setEnabled(False)
        self.llm_test_button.setText(tr("Testing..."))
        self.test_llm_requested.emit(copy.deepcopy(self.values["llm"]), self.llm_key.text().strip())

    def restore_test_buttons(self) -> None:
        self.asr_test_button.setEnabled(True)
        self.asr_test_button.setText(tr("Test with a WAV file"))
        self.llm_test_button.setEnabled(True)
        self.llm_test_button.setText(tr("Test composer connection"))
