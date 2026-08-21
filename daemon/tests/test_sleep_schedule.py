from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

import forcefocus_daemon
from forcefocus.api_http import EmbeddedWebHandler
import forcefocus.sleep_schedule as sleep_schedule
from forcefocus.state_store import StateStore, StateStoreError
from forcefocus.utils import get_continuous_time


def _schedule(**overrides):
    schedule = {
        "enabled": True,
        "days_of_week": [0, 1, 2, 3, 4, 5, 6],
        "sleep_time": "22:00",
        "wake_time": "07:00",
        "mode": "blacklist",
        "blacklist": ["blocked.example"],
        "whitelist": [],
    }
    schedule.update(overrides)
    return schedule


def test_sleep_schedule_validates_times_and_selected_domains(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager

    equal = manager.cmd_save_sleep_schedule(_schedule(wake_time="22:00"))
    empty = manager.cmd_save_sleep_schedule(_schedule(blacklist=[]))
    invalid = manager.cmd_save_sleep_schedule(_schedule(whitelist=["not a domain"], mode="whitelist"))

    assert equal["status"] == "error"
    assert empty["status"] == "error"
    assert invalid["status"] == "error"

    dispatched = mock_daemon.command_service.dispatch(
        {"action": "save_sleep_schedule", **_schedule(wake_time="22:00")}
    )
    assert dispatched["error_code"] == "INVALID_INPUT"


def test_sleep_schedule_rejects_selected_domains_beyond_dnr_capacity(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    response = manager.cmd_save_sleep_schedule(
        _schedule(blacklist=[f"site{index}.example" for index in range(2001)])
    )

    assert response["status"] == "error"
    assert "at most 2000 selected sites" in response["message"]


def test_sleep_capacity_accounts_for_current_permanent_rules(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    mock_daemon.perma_blocklist = [f"permanent{index}.example" for index in range(501)]

    rejected = manager.cmd_save_sleep_schedule(
        _schedule(blacklist=[f"site{index}.example" for index in range(2000)])
    )
    accepted = manager.cmd_save_sleep_schedule(
        _schedule(blacklist=[f"site{index}.example" for index in range(1999)])
    )

    assert rejected["status"] == "error"
    assert "at most 1999 selected sites with 501 permanent sites" in rejected["message"]
    assert accepted["status"] == "ok"


def test_sleep_whitelist_capacity_includes_static_rules(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    mock_daemon.perma_blocklist = [f"permanent{index}.example" for index in range(1499)]

    response = manager.cmd_save_sleep_schedule(
        _schedule(
            mode="whitelist",
            blacklist=[],
            whitelist=[f"site{index}.example" for index in range(2000)],
        )
    )

    assert response["status"] == "error"
    assert "at most 1998 selected sites with 1499 permanent sites" in response["message"]


def test_invalid_existing_sleep_schedule_requires_recovery_and_preserves_file(mock_daemon):
    path = sleep_schedule.SLEEP_SCHEDULE_FILE
    path.write_text('{"enabled": true,')
    original = path.read_text()

    with pytest.raises(StateStoreError, match="Sleep Schedule recovery required"):
        mock_daemon.sleep_schedule_manager.load()

    assert path.read_text() == original
    assert mock_daemon.recovery_required is True


def test_invalid_current_sleep_schedule_schema_requires_recovery(mock_daemon):
    path = sleep_schedule.SLEEP_SCHEDULE_FILE
    invalid = {
        **sleep_schedule.DEFAULT_SLEEP_SCHEDULE,
        "enabled": "true",
        "pending_config": None,
        "pending_apply_at": None,
    }
    StateStore.write_json(path, invalid, indent=2)
    original = path.read_text()

    with pytest.raises(StateStoreError, match="Sleep Schedule recovery required"):
        mock_daemon.sleep_schedule_manager.load()

    assert path.read_text() == original
    assert mock_daemon.recovery_required is True


def test_missing_sleep_schedule_is_initialized_as_first_run(mock_daemon):
    path = sleep_schedule.SLEEP_SCHEDULE_FILE
    path.unlink(missing_ok=True)

    mock_daemon.sleep_schedule_manager.load()

    assert mock_daemon.state_store.read_json(path)["enabled"] is False
    assert mock_daemon.recovery_required is False


def test_cross_midnight_sleep_occurrence_uses_start_day(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    monday = datetime(2025, 1, 6, 23, 0)
    manager.schedule = _schedule(days_of_week=[0])

    active = manager.active_occurrence(monday + timedelta(hours=3))
    next_occurrence = manager.next_occurrence(monday + timedelta(hours=9))

    assert active[0] == datetime(2025, 1, 6, 22, 0)
    assert active[1] == datetime(2025, 1, 7, 7, 0)
    assert next_occurrence[0] == datetime(2025, 1, 13, 22, 0)


def test_sleep_start_uses_snapshot_and_fixed_wake_deadline(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    now = datetime.now().replace(hour=23, minute=30, second=0, microsecond=0)
    manager.schedule = _schedule()
    command = manager.start_if_due(now)
    mock_daemon.events.emit = MagicMock()

    result = mock_daemon.session_manager._start_session(command)

    assert result["status"] == "ok"
    assert mock_daemon.state.session.session_type == "sleep"
    assert mock_daemon.state.session.session_expiry == command["_sleep_wake"]
    assert mock_daemon.session_base_domains == ["blocked.example"]
    assert "blocked.example" in mock_daemon.state.active_domains
    persisted = mock_daemon.state_store.read_json(forcefocus_daemon.SESSION_LOCK)
    assert persisted["sleep_occurrence"] == command["_sleep_occurrence"]
    assert persisted["expiry"] == command["_sleep_wake"].isoformat()


def test_sleep_edits_are_queued_and_early_stop_suppresses_occurrence(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    manager.schedule = {**_schedule(), "suppressed_occurrences": []}
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "sleep"
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(hours=2)

    saved = manager.cmd_save_sleep_schedule(_schedule(blacklist=["next.example"]))
    manager.suppress_current_occurrence("2025-01-06T22:00:00")

    assert saved["status"] == "ok"
    assert saved["queued"] is True
    assert manager.schedule["blacklist"] == ["blocked.example"]
    assert manager.pending_config["blacklist"] == ["next.example"]
    assert saved["apply_at"] == mock_daemon.state.session.session_expiry.isoformat()
    assert "2025-01-06T22:00:00" in manager.schedule["suppressed_occurrences"]
    persisted = mock_daemon.state_store.read_json(sleep_schedule.SLEEP_SCHEDULE_FILE)
    assert persisted["blacklist"] == ["blocked.example"]
    assert persisted["pending_config"]["blacklist"] == ["next.example"]
    mock_daemon.state.session.active = False
    assert manager.start_if_due(datetime(2025, 1, 6, 23, 0)) is None


def test_early_stopped_sleep_promotes_pending_edits_at_fixed_wake(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    wake = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    occurrence = wake - timedelta(hours=3)
    manager.schedule = {
        **_schedule(
            days_of_week=[occurrence.weekday()],
            sleep_time=occurrence.strftime("%H:%M"),
            wake_time=wake.strftime("%H:%M"),
        ),
        "suppressed_occurrences": [],
    }
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "sleep"
    mock_daemon.state.session.session_expiry = wake

    saved = manager.cmd_save_sleep_schedule(_schedule(blacklist=["next.example"]))
    manager.suppress_current_occurrence(occurrence.isoformat())
    mock_daemon.state.session.active = False

    assert saved["queued"] is True
    assert manager.start_if_due(wake - timedelta(seconds=1)) is None
    assert manager.schedule["blacklist"] == ["blocked.example"]

    manager.start_if_due(wake)

    assert manager.schedule["blacklist"] == ["next.example"]
    assert manager.pending_config is None
    assert manager.pending_apply_at is None


def test_sleep_cleanup_persists_suppression_before_removing_session_lock(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    manager.schedule = {**_schedule(), "suppressed_occurrences": []}
    mock_daemon.events.emit = MagicMock()
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "sleep"
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.sleep_occurrence = "2025-01-06"
    mock_daemon.state.session.pending_unlock_at = datetime.now()
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(hours=1)
    mock_daemon.state.session.total_duration_seconds = 3600
    mock_daemon._mono_session_end = get_continuous_time() + 3600

    mock_daemon.session_manager._cleanup_session()

    assert "2025-01-06" in manager.schedule["suppressed_occurrences"]
    assert mock_daemon.history_manager.load_history() == []


def test_sleep_natural_wake_with_pending_unlock_does_not_suppress(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    manager.schedule = {**_schedule(), "suppressed_occurrences": []}
    manager.pending_config = _schedule(blacklist=["next.example"])
    manager.pending_apply_at = datetime.now() - timedelta(seconds=1)
    mock_daemon.events.emit = MagicMock()
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "sleep"
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.sleep_occurrence = "2025-01-06"
    mock_daemon.state.session.pending_unlock_at = datetime.now() - timedelta(seconds=1)
    mock_daemon.state.session.session_expiry = datetime.now() - timedelta(seconds=1)
    mock_daemon._mono_session_end = get_continuous_time() - 1

    mock_daemon.session_manager._cleanup_session()

    assert manager.schedule["suppressed_occurrences"] == []
    assert manager.schedule["blacklist"] == ["next.example"]
    assert manager.pending_config is None


def test_pending_sleep_edits_promote_at_fixed_wake(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    manager.schedule = {**_schedule(), "suppressed_occurrences": ["2025-01-06"]}
    pending = _schedule(blacklist=["next.example"])
    manager.pending_config = pending
    manager.pending_apply_at = datetime.now() + timedelta(minutes=1)

    assert not manager.promote_pending_if_due(datetime.now())
    assert manager.schedule["blacklist"] == ["blocked.example"]
    assert manager.promote_pending_if_due(datetime.now() + timedelta(minutes=2))
    assert manager.schedule["blacklist"] == ["next.example"]
    assert manager.schedule["suppressed_occurrences"] == ["2025-01-06"]
    assert manager.pending_config is None


def test_sleep_start_reenforces_active_prayer_ban(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    now = datetime.now().replace(hour=23, minute=30, second=0, microsecond=0)
    manager.schedule = _schedule()
    mock_daemon.events.emit = MagicMock()
    mock_daemon.prayer_ban_active = "Isha"
    mock_daemon.watchdog_manager._enforce_prayer_ban = MagicMock()

    result = mock_daemon.session_manager._start_session(manager.start_if_due(now))

    assert result["status"] == "ok"
    mock_daemon.watchdog_manager._enforce_prayer_ban.assert_called_once()


def test_sleep_conflicts_reject_oneoff_and_recurring_sessions(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    manager.schedule = {**_schedule(), "suppressed_occurrences": []}
    monday = datetime(2025, 1, 6, 23, 0)
    mock_daemon.schedules_manager._next_recurring_run = MagicMock()

    assert manager.conflicts_interval(monday, monday + timedelta(minutes=30))
    assert manager.conflicts_recurring(
        {
            "enabled": True,
            "days_of_week": [0],
            "start_time": "23:00",
            "duration_minutes": 30,
        }
    )


def test_sleep_status_summary_excludes_domain_lists(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    manager.schedule = {**_schedule(), "suppressed_occurrences": []}

    summary = manager.status_summary()

    assert {"enabled", "active", "mode", "days_of_week", "sleep_time", "wake_time", "wake_at", "next_start_at", "remaining_seconds", "pending_changes"} <= set(summary)
    assert "blacklist" not in summary
    assert "whitelist" not in summary


def test_active_session_cannot_extend_into_sleep(mock_daemon):
    manager = mock_daemon.sleep_schedule_manager
    now = datetime.now().replace(second=0, microsecond=0)
    sleep_start = now + timedelta(minutes=30)
    wake = sleep_start + timedelta(hours=1)
    manager.schedule = {
        **_schedule(
            days_of_week=[sleep_start.weekday()],
            sleep_time=sleep_start.strftime("%H:%M"),
            wake_time=wake.strftime("%H:%M"),
        ),
        "suppressed_occurrences": [],
    }
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "standard"
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_expiry = now + timedelta(minutes=10)

    result = mock_daemon.command_service.dispatch(
        {"action": "start", "duration_minutes": 60, "mode": "blacklist"}
    )

    assert result["status"] == "error"
    assert result["error_code"] == "STATE_CONFLICT"
    assert "Sleep Schedule" in result["message"]


def test_restored_sleep_keeps_its_fixed_wake_deadline(mock_daemon):
    wake = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    occurrence = wake - timedelta(hours=3)
    mock_daemon.sleep_schedule_manager.schedule = {
        **_schedule(
            days_of_week=[occurrence.weekday()],
            sleep_time=occurrence.strftime("%H:%M"),
            wake_time=wake.strftime("%H:%M"),
        ),
        "suppressed_occurrences": [],
    }
    StateStore.write_json(
        forcefocus_daemon.SESSION_LOCK,
        {
            "expiry": wake.isoformat(),
            "duration_minutes": 120,
            "mode": "blacklist",
            "session_type": "sleep",
            "sleep_occurrence": occurrence.isoformat(),
            "active_domains": ["blocked.example"],
            "session_base_domains": ["blocked.example"],
        },
    )
    mock_daemon.enforcement_manager._enforce_block = MagicMock()

    mock_daemon._restore_session()

    assert mock_daemon.state.session.session_type == "sleep"
    assert mock_daemon.state.session.session_expiry == wake
    assert mock_daemon.state.session.sleep_occurrence == occurrence.isoformat()


@pytest.mark.parametrize(
    "changes",
    [
        {"mode": "invalid"},
        {"expiry": "2000-01-01T00:00:00"},
        {"sleep_occurrence": "not-an-occurrence"},
        {"session_base_domains": [], "active_domains": []},
    ],
)
def test_invalid_restored_sleep_lock_requires_recovery_without_overwriting(mock_daemon, changes):
    wake = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    occurrence = wake - timedelta(hours=3)
    mock_daemon.sleep_schedule_manager.schedule = {
        **_schedule(
            days_of_week=[occurrence.weekday()],
            sleep_time=occurrence.strftime("%H:%M"),
            wake_time=wake.strftime("%H:%M"),
        ),
        "suppressed_occurrences": [],
    }
    persisted = {
        "expiry": wake.isoformat(),
        "duration_minutes": 120,
        "mode": "blacklist",
        "session_type": "sleep",
        "sleep_occurrence": occurrence.isoformat(),
        "active_domains": ["blocked.example"],
        "session_base_domains": ["blocked.example"],
    }
    persisted.update(changes)
    StateStore.write_json(forcefocus_daemon.SESSION_LOCK, persisted)
    original = forcefocus_daemon.SESSION_LOCK.read_text()

    with pytest.raises(StateStoreError, match="Sleep session recovery required"):
        mock_daemon._restore_session()

    assert forcefocus_daemon.SESSION_LOCK.read_text() == original
    assert mock_daemon.state.session.active is False
    assert mock_daemon.recovery_required is True


def test_restored_sleep_ban_does_not_require_domain_snapshots(mock_daemon):
    wake = (datetime.now() + timedelta(hours=2)).replace(second=0, microsecond=0)
    occurrence = wake - timedelta(hours=3)
    mock_daemon.sleep_schedule_manager.schedule = {
        **_schedule(
            mode="ban",
            blacklist=[],
            days_of_week=[occurrence.weekday()],
            sleep_time=occurrence.strftime("%H:%M"),
            wake_time=wake.strftime("%H:%M"),
        ),
        "suppressed_occurrences": [],
    }
    StateStore.write_json(
        forcefocus_daemon.SESSION_LOCK,
        {
            "expiry": wake.isoformat(),
            "duration_minutes": 120,
            "mode": "ban",
            "session_type": "sleep",
            "sleep_occurrence": occurrence.isoformat(),
        },
    )
    mock_daemon.enforcement_manager._enforce_whitelist = MagicMock()

    mock_daemon._restore_session()

    assert mock_daemon.state.session.active is True
    assert mock_daemon.state.session.mode == "ban"


def test_sleep_schedule_http_routes_dispatch_authenticated_commands(mock_daemon):
    handler = EmbeddedWebHandler.__new__(EmbeddedWebHandler)
    handler.path = "/api/sleep-schedule"
    handler._is_host_allowed = lambda: True
    handler._is_origin_allowed = lambda: True
    handler._is_api_token_valid = lambda: True
    handler._dispatch = MagicMock()

    handler.do_GET()
    handler._dispatch.assert_called_once_with({"action": "get_sleep_schedule"})

    handler._dispatch.reset_mock()
    handler._read_body = lambda: _schedule()
    handler.do_POST()
    assert handler._dispatch.call_args.args[0]["action"] == "save_sleep_schedule"
    assert handler._dispatch.call_args.args[0]["blacklist"] == ["blocked.example"]


def test_session_domains_requires_api_token(mock_daemon):
    handler = EmbeddedWebHandler.__new__(EmbeddedWebHandler)
    handler.path = "/api/session-domains"
    handler._is_host_allowed = lambda: True
    handler._is_origin_allowed = lambda: True
    handler._is_api_token_valid = lambda: False
    handler._send_json = MagicMock()

    handler.do_GET()

    assert handler._send_json.call_args.args[0]["error_code"] == "UNAUTHORIZED"
    assert handler._send_json.call_args.args[1] == 401
