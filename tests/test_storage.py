from __future__ import annotations

import json

from voice_context.storage.config_store import ConfigStore, DEFAULT_CONFIG
from voice_context.storage.session_store import SessionStore


def test_config_merges_defaults_and_never_defines_api_keys(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"general": {"theme": "dark"}}), encoding="utf-8")
    store = ConfigStore(path)
    assert store.get("general.theme") == "dark"
    assert store.get("hotkeys.finish")
    store.save()
    persisted = path.read_text(encoding="utf-8")
    assert "api_key" not in persisted
    assert "api_key" not in json.dumps(DEFAULT_CONFIG)


def test_config_recovers_from_invalid_json(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{bad", encoding="utf-8")
    store = ConfigStore(path)
    assert store.get("general.theme") == "system"
    assert (tmp_path / "config.broken.json").exists()


def test_config_reload_reads_changes_saved_by_another_instance(tmp_path) -> None:
    path = tmp_path / "config.json"
    running_store = ConfigStore(path)
    running_store.set("llm.enabled", False)
    running_store.save()

    settings_store = ConfigStore(path)
    settings_store.set("llm.enabled", True)
    settings_store.save()

    assert running_store.get("llm.enabled") is False
    running_store.reload()
    assert running_store.get("llm.enabled") is True


def test_session_store_crud_and_cleanup(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    payload = {
        "id": "abc",
        "createdAtMs": 1000,
        "endedAtMs": 2000,
        "transcript": "hello",
        "finalPrompt": "task",
        "sourceApp": "terminal.exe",
        "events": [{"type": "reference", "id": "ref_1", "content": "x"}],
    }
    store.save(payload)
    assert store.recent()[0]["reference_count"] == 1
    assert store.get("abc")["finalPrompt"] == "task"
    store.delete("abc")
    assert store.get("abc") is None
