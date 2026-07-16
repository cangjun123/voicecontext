from __future__ import annotations

from typing import Final

import keyring


SERVICE_NAME: Final = "VoiceContext"


class SecretStore:
    """Stores API credentials using Windows Credential Manager through keyring."""

    def get(self, name: str) -> str:
        try:
            return keyring.get_password(SERVICE_NAME, name) or ""
        except keyring.errors.KeyringError:
            return ""

    def set(self, name: str, value: str) -> None:
        try:
            if value:
                keyring.set_password(SERVICE_NAME, name, value)
            else:
                self.delete(name)
        except keyring.errors.KeyringError as exc:
            raise RuntimeError("Windows Credential Manager is unavailable; API key was not saved.") from exc

    def delete(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, name)
        except (keyring.errors.KeyringError, keyring.errors.PasswordDeleteError):
            pass

    @staticmethod
    def key_for(section: str, provider: str) -> str:
        return f"{section}:{provider}:api_key"
