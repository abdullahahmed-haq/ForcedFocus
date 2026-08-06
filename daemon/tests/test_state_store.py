import json

import pytest

from forcefocus.state_store import StateStore, StateStoreError
from forcefocus.version import STATE_SCHEMA_VERSION


def test_write_json_is_readable_and_creates_a_manifest(tmp_path):
    store = StateStore(tmp_path)
    state_path = tmp_path / "lists.json"
    StateStore.write_json(state_path, {"blacklist": ["example.com"]}, indent=2)

    manifest = store.ensure_schema()

    assert store.read_json(state_path) == {
        "blacklist": ["example.com"],
        "whitelist": [],
    }
    assert manifest["schema_version"] == STATE_SCHEMA_VERSION
    assert manifest["files"]["lists.json"] == STATE_SCHEMA_VERSION
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_session_backup_keeps_last_valid_document(tmp_path):
    store = StateStore(tmp_path)
    session_path = tmp_path / "session.lock"
    previous_path = tmp_path / "session.lock.prev"
    StateStore.write_json(session_path, {"expiry": "2030-01-01T00:00:00"})

    store.backup_session_lock(session_path, previous_path)
    session_path.write_text("{not valid json", encoding="utf-8")

    assert store.read_json(session_path) is None
    assert store.read_json(previous_path) == {"expiry": "2030-01-01T00:00:00"}


def test_schema_creation_refuses_malformed_legacy_state(tmp_path):
    (tmp_path / "settings.json").write_text("{bad", encoding="utf-8")

    with pytest.raises(StateStoreError, match="settings.json"):
        StateStore(tmp_path).ensure_schema()

    backup = next((tmp_path / "backups").iterdir()) / "settings.json"
    assert backup.read_text(encoding="utf-8") == "{bad"
