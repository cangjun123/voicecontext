from __future__ import annotations

import json
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

from keyring.errors import KeyringError

from .config_store import APP_DIR, ConfigStore
from .secrets import SecretStore

PROFILE_FILES = ("config.json", "history.sqlite3")
PROFILE_KEYS = ("asr:aliyun_realtime:api_key", "llm:openai_compatible:api_key", "asr:openai_compatible:api_key")
SETTINGS_FORMAT = "voicecontext-settings"


class ProfileError(RuntimeError):
    pass


def read_settings_bundle(path: Path) -> tuple[dict, dict[str, str]]:
    """Load a settings JSON exported from the Settings window."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"Could not read the settings file: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("format") != SETTINGS_FORMAT:
        raise ProfileError("This file is not a VoiceContext settings export.")
    settings = payload.get("settings")
    secrets = payload.get("secrets", {})
    if not isinstance(settings, dict) or not isinstance(secrets, dict):
        raise ProfileError("The settings file is malformed.")
    return settings, {str(k): str(v) for k, v in secrets.items() if isinstance(v, str)}


def write_settings_bundle(path: Path, settings: dict, secrets: dict[str, str]) -> Path:
    """Write a single-file settings export containing every setting and API key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": SETTINGS_FORMAT,
        "version": 1,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "settings": settings,
        "secrets": {k: v for k, v in secrets.items() if v},
    }
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return path


def export_profile(archive_path: Path) -> Path:
    """Bundle settings, history, and stored API keys into one portable archive."""
    archive_path = archive_path.with_suffix(".zip") if archive_path.suffix != ".zip" else archive_path
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    missing = [name for name in PROFILE_FILES if not (APP_DIR / name).exists()]
    if missing:
        raise ProfileError(f"Nothing to export: {', '.join(missing)} not found in {APP_DIR}")

    secrets = SecretStore()
    credentials: dict[str, str] = {}
    for name in PROFILE_KEYS:
        try:
            value = secrets.get(name)
        except KeyringError:
            value = ""
        if value:
            credentials[name] = value

    temp = archive_path.with_suffix(".tmp.zip")
    snapshot = archive_path.with_suffix(".snapshot.sqlite3")
    try:
        with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(APP_DIR / "config.json", "config.json")
            # SQLite must be read through the backup API, not file-copy, so a session
            # being written while we export cannot corrupt the snapshot.
            source = sqlite3.connect(APP_DIR / "history.sqlite3")
            try:
                destination = sqlite3.connect(snapshot)
                try:
                    with destination:
                        source.backup(destination)
                finally:
                    destination.close()
            finally:
                source.close()
            bundle.write(snapshot, "history.sqlite3")
            bundle.writestr("credentials.json", json.dumps(credentials, ensure_ascii=False, indent=2))
        temp.replace(archive_path)
    finally:
        temp.unlink(missing_ok=True)
        snapshot.unlink(missing_ok=True)
    return archive_path


def import_profile(source: Path) -> dict[str, int]:
    """Restore an exported profile; existing files are moved aside first."""
    extracted: Path | None = None
    if source.is_dir():
        root = source
        members = {name: root / name for name in PROFILE_FILES if (root / name).exists()}
        credentials = _read_credentials_json(root / "credentials.json")
    elif source.is_file():
        try:
            with zipfile.ZipFile(source) as bundle:
                names = set(bundle.namelist())
                members = {name: None for name in PROFILE_FILES if name in names}
                bundle.extractall(root := source.with_suffix(".extracted"))
                members = {name: root / name for name in members}
                credentials = _read_credentials_json(root / "credentials.json")
        except zipfile.BadZipFile as exc:
            raise ProfileError(f"Not a VoiceContext profile archive: {source}") from exc
        extracted = root
    else:
        raise ProfileError(f"Profile source not found: {source}")

    missing = [name for name in PROFILE_FILES if name not in members]
    if missing:
        raise ProfileError(f"The profile is incomplete: missing {', '.join(missing)}")

    APP_DIR.mkdir(parents=True, exist_ok=True)
    restored = 0
    backup_dir = APP_DIR / f"backup-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        for name, member_path in members.items():
            target = APP_DIR / name
            if target.exists():
                backup_dir.mkdir(exist_ok=True)
                shutil.move(str(target), backup_dir / name)
            shutil.copy2(member_path, target)
            restored += 1
    finally:
        if extracted is not None:
            shutil.rmtree(extracted, ignore_errors=True)

    keys = 0
    if credentials:
        secrets = SecretStore()
        for name, value in credentials.items():
            try:
                secrets.set(name, value)
                keys += 1
            except RuntimeError as exc:
                raise ProfileError(f"Credentials could not be restored: {exc}") from exc

    # Device names and hotkey-scoped settings rarely survive a machine move.
    _drop_stale_device_setting(APP_DIR)
    return {"files_restored": restored, "keys_restored": keys}


def _read_credentials_json(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError(f"The profile's credentials file is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError("The profile's credentials file is malformed.")
    return {str(k): str(v) for k, v in data.items() if v}


def _drop_stale_device_setting(app_dir: Path) -> None:
    """Clear the audio input device name so the first run does not fail on new hardware."""
    store = ConfigStore(app_dir / "config.json")
    device = str(store.get("asr.input_device", ""))
    if device:
        store.set("asr.input_device", "")
        store.save()
