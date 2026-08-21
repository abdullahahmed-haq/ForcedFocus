import json

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

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


def test_manifest_v1_migrates_sleep_state_to_v2(tmp_path):
    StateStore.write_json(
        tmp_path / "state_manifest.json",
        {
            "product_version": "1.0.0",
            "schema_version": 1,
            "files": {"lists.json": 1},
        },
    )

    manifest = StateStore(tmp_path).ensure_schema()
    sleep = StateStore(tmp_path).read_json(tmp_path / "sleep_schedule.json")

    assert manifest["schema_version"] == 2
    assert set(manifest["files"].values()) == {2}
    assert sleep == {
        "enabled": False,
        "days_of_week": [],
        "sleep_time": "22:00",
        "wake_time": "07:00",
        "mode": "blacklist",
        "blacklist": [],
        "whitelist": [],
        "suppressed_occurrences": [],
        "pending_config": None,
        "pending_apply_at": None,
    }
    assert any((tmp_path / "backups").iterdir())


def test_manifest_v1_migration_rolls_back_on_invalid_sleep_state(tmp_path):
    manifest_path = tmp_path / "state_manifest.json"
    StateStore.write_json(
        manifest_path,
        {"product_version": "1.0.0", "schema_version": 1, "files": {}},
    )
    (tmp_path / "sleep_schedule.json").write_text("{bad", encoding="utf-8")

    with pytest.raises(StateStoreError, match="sleep_schedule.json"):
        StateStore(tmp_path).ensure_schema()

    assert StateStore(tmp_path).read_json(manifest_path)["schema_version"] == 1
    assert (tmp_path / "sleep_schedule.json").read_text(encoding="utf-8") == "{bad"


def test_blacklist_session_restore_uses_active_domains(mock_daemon, monkeypatch):
    import forcefocus_daemon

    session_path = forcefocus_daemon.SESSION_LOCK
    previous_path = session_path.with_name("session.lock.prev")
    monkeypatch.setattr(forcefocus_daemon, "SESSION_LOCK_PREVIOUS", previous_path)
    StateStore.write_json(session_path, {
        "expiry": (datetime.now() + timedelta(minutes=30)).isoformat(),
        "duration_minutes": 30,
        "mode": "blacklist",
        "session_type": "standard",
        "active_domains": ["blocked.example"],
        "session_base_domains": ["blocked.example"],
    })
    mock_daemon.enforcement_manager._enforce_block = MagicMock()

    mock_daemon._restore_session()

    assert mock_daemon.state.session.active is True
    assert mock_daemon.state.active_domains == ["blocked.example"]
    mock_daemon.enforcement_manager._enforce_block.assert_called_once()
