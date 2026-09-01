from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from voice_context.storage import profile
from voice_context.storage.profile import ProfileError, export_profile, import_profile


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str:
        return self.values.get(name, "")

    def set(self, name: str, value: str) -> None:
        if value:
            self.values[name] = value
        else:
            self.values.pop(name, None)

    def delete(self, name: str) -> None:
        self.values.pop(name, None)

    @staticmethod
    def key_for(section: str, provider: str) -> str:
        return f"{section}:{provider}:api_key"


@pytest.fixture
def app_dir(tmp_path, monkeypatch) -> Path:
    data = tmp_path / "VoiceContext"
    data.mkdir()
    monkeypatch.setattr(profile, "APP_DIR", data)
    return data


@pytest.fixture
def secrets(monkeypatch) -> MemorySecrets:
    store = MemorySecrets()
    monkeypatch.setattr(profile, "SecretStore", lambda: store)
    return store


def _seed_profile(app_dir: Path, secrets: MemorySecrets, with_history: bool = True) -> None:
    (app_dir / "config.json").write_text(
        json.dumps({"asr": {"provider": "aliyun_realtime", "input_device": "Old Microphone"}}),
        encoding="utf-8",
    )
    if with_history:
        connection = sqlite3.connect(app_dir / "history.sqlite3")
        try:
            connection.execute(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, data_json TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO sessions VALUES ('s1', '{}')")
            connection.commit()
        finally:
            connection.close()
    secrets.set("asr:aliyun_realtime:api_key", "sk-asr")
    secrets.set("llm:openai_compatible:api_key", "sk-llm")


def test_export_bundles_settings_history_and_keys(tmp_path, app_dir, secrets) -> None:
    _seed_profile(app_dir, secrets)
    archive = export_profile(tmp_path / "migrate.zip")

    assert archive.exists()
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {"config.json", "history.sqlite3", "credentials.json"}
        credentials = json.loads(bundle.read("credentials.json"))
    assert credentials == {
        "asr:aliyun_realtime:api_key": "sk-asr",
        "llm:openai_compatible:api_key": "sk-llm",
    }
    # The exported database must contain the seeded session.
    export_copy = tmp_path / "exported.sqlite3"
    with zipfile.ZipFile(archive) as bundle:
        export_copy.write_bytes(bundle.read("history.sqlite3"))
    with sqlite3.connect(export_copy) as connection:
        exported_rows = connection.execute("SELECT id FROM sessions").fetchall()
    assert exported_rows == [("s1",)]


def test_import_restores_files_backs_up_existing_and_clears_device(
    tmp_path, app_dir, secrets, monkeypatch
) -> None:
    _seed_profile(app_dir, secrets)
    archive = export_profile(tmp_path / "migrate.zip")

    # Simulate a new machine: existing config with a different device, no history.
    (app_dir / "config.json").write_text(
        json.dumps({"asr": {"provider": "openai_compatible", "input_device": "Other Mic"}}),
        encoding="utf-8",
    )
    (app_dir / "history.sqlite3").unlink()
    secrets.set("asr:aliyun_realtime:api_key", "")

    summary = import_profile(archive)

    assert summary == {"files_restored": 2, "keys_restored": 2}
    config = json.loads((app_dir / "config.json").read_text(encoding="utf-8"))
    assert config["asr"]["provider"] == "aliyun_realtime"
    assert config["asr"]["input_device"] == ""  # stale device cleared for new hardware
    assert secrets.get("asr:aliyun_realtime:api_key") == "sk-asr"
    assert secrets.get("llm:openai_compatible:api_key") == "sk-llm"
    backups = list(app_dir.glob("backup-*"))
    assert len(backups) == 1
    old = json.loads((backups[0] / "config.json").read_text(encoding="utf-8"))
    assert old["asr"]["input_device"] == "Other Mic"


def test_import_from_extracted_folder(tmp_path, app_dir, secrets) -> None:
    _seed_profile(app_dir, secrets)
    archive = export_profile(tmp_path / "migrate.zip")
    folder = tmp_path / "unpacked"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(folder)

    summary = import_profile(folder)

    assert summary["files_restored"] == 2


def test_import_rejects_incomplete_profile(tmp_path, app_dir, secrets) -> None:
    folder = tmp_path / "incomplete"
    folder.mkdir()
    (folder / "config.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ProfileError, match="incomplete"):
        import_profile(folder)


def test_export_reports_missing_data(tmp_path, app_dir, secrets) -> None:
    with pytest.raises(ProfileError, match="Nothing to export"):
        export_profile(tmp_path / "empty.zip")
    assert not (tmp_path / "empty.zip").exists()


def test_import_rejects_non_profile_zip(tmp_path, app_dir, secrets) -> None:
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as bundle:
        bundle.writestr("readme.txt", "not a profile")

    with pytest.raises(ProfileError, match="incomplete"):
        import_profile(bad)
