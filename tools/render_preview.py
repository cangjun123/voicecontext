from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from voice_context.core.session import SessionState, VoiceSession
from voice_context.storage.config_store import ConfigStore
from voice_context.ui.main_window import MainWindow
from voice_context.ui.i18n import set_language
from voice_context.ui.overlay import OverlayWindow
from voice_context.ui.settings_window import SettingsWindow
from voice_context.ui.theme import apply_theme


class PreviewSecrets:
    def get(self, name: str) -> str:
        return ""

    def set(self, name: str, value: str) -> None:
        pass


def main() -> None:
    app = QApplication([])
    set_language("zh_CN")
    apply_theme(app, "light")
    output = Path(__file__).resolve().parents[1] / "artifacts"
    output.mkdir(parents=True, exist_ok=True)

    session = VoiceSession(source_app="WindowsTerminal.exe")
    session.transition(SessionState.LISTENING)
    session.add_speech("帮我检查登录流程为什么会失败", 0, 1800)
    session.add_reference(
        "src/auth/session.ts",
        content_type="file",
        captured_at_ms=2100,
        source_process="Code.exe",
        source_window="session.ts - Visual Studio Code",
    )
    session.add_reference(
        "Error: invalid session token",
        content_type="error",
        captured_at_ms=2700,
        source_process="WindowsTerminal.exe",
        source_window="project - PowerShell",
    )
    session.add_speech("根据这个错误直接给我 patch", 3100, 4300)
    session.final_prompt = (
        "Task:\n帮我检查 [ref_1] 的登录流程，并根据 [ref_2] 修复 token 校验失败。\n\n"
        "References:\n\n[ref_1]\n```text\nsrc/auth/session.ts\n```\n\n"
        "[ref_2]\n```text\nError: invalid session token\n```"
    )

    main_window = MainWindow()
    main_window.set_session(session)
    main_window.show()
    app.processEvents()
    main_window.grab().save(str(output / "main-window.png"))
    main_window.hide()

    config = ConfigStore(Path(tempfile.mkdtemp()) / "config.json")
    settings = SettingsWindow(config, PreviewSecrets())
    settings.show()
    app.processEvents()
    settings.grab().save(str(output / "settings-window.png"))
    settings.hide()

    overlay = OverlayWindow(config.section("general"))
    overlay.set_state("Listening", session.transcript)
    overlay.set_references(session.references)
    overlay.resize(460, overlay.sizeHint().height())
    overlay.show()
    app.processEvents()
    overlay.grab().save(str(output / "overlay.png"))
    overlay.close()


if __name__ == "__main__":
    main()
