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
    session.set_partial("帮我把这个文件")
    session.add_reference(
        "src/auth/session.ts",
        content_type="file",
        captured_at_ms=2100,
        source_process="Code.exe",
        source_window="session.ts - Visual Studio Code",
    )
    session.set_partial("帮我把这个文件和这个错误")
    session.add_reference(
        "Error: invalid session token",
        content_type="error",
        captured_at_ms=2700,
        source_process="WindowsTerminal.exe",
        source_window="project - PowerShell",
    )
    session.set_partial("")
    session.add_speech("帮我把这个文件和这个错误结合起来，直接给我 patch", 0, 4300)
    session.final_prompt = "根据 src/auth/session.ts 和 Error: invalid session token 修复登录流程。"
    session.asr_metadata = {"status": "success", "model": "fun-asr-realtime"}
    session.composer_metadata = {
        "requested": True,
        "used": True,
        "response_model": "deepseek-v4-flash",
        "latency_ms": 2380,
        "usage": {"prompt_tokens": 318, "completion_tokens": 142},
    }
    session.output_metadata = {"status": "copied", "clipboard_verified": True}
    session.transition(SessionState.COMPOSING)
    session.transition(SessionState.PROMPT_READY)

    main_window = MainWindow()
    main_window.set_session(session)
    main_window.set_completion_receipt(session)
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
    session.state = SessionState.LISTENING
    overlay.set_session_timeline(session, highlighted_id="ref_2")
    overlay.set_state("Listening")
    overlay.resize(460, overlay.sizeHint().height())
    overlay.show()
    app.processEvents()
    overlay.grab().save(str(output / "overlay.png"))
    overlay.show_completion(session.final_prompt, len(session.references), "copied")
    app.processEvents()
    overlay.grab().save(str(output / "overlay-completion.png"))
    overlay.close()


if __name__ == "__main__":
    main()
