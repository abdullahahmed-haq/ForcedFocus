from datetime import datetime, timedelta
from unittest.mock import MagicMock


def _rule_payload(start_time, **overrides):
    payload = {
        "name": "Focus Ritual",
        "days_of_week": [datetime.now().weekday()],
        "start_time": start_time,
        "duration_minutes": 60,
        "mode": "blacklist",
        "session_type": "standard",
        "groups": [],
    }
    payload.update(overrides)
    return payload


def test_recurring_schedule_cannot_pause_inside_twenty_minutes(mock_daemon):
    next_run = datetime.now() + timedelta(minutes=10)
    created = mock_daemon.schedules_manager.cmd_add_recurring_schedule(
        _rule_payload(next_run.strftime("%H:%M"))
    )

    response = mock_daemon.schedules_manager.cmd_toggle_recurring_schedule(
        {"id": created["rule"]["id"]}, False
    )

    assert response["status"] == "error"
    assert "20 minutes" in response["message"]
    assert mock_daemon.recurring_schedules[0]["enabled"] is True


def test_recurring_skip_prevents_watchdog_start(mock_daemon):
    now = datetime.now()
    start_time = (now - timedelta(minutes=1)).strftime("%H:%M")
    created = mock_daemon.schedules_manager.cmd_add_recurring_schedule(
        _rule_payload(start_time, skip_next_date=now.strftime("%Y-%m-%d"))
    )
    mock_daemon._persist_session_lock = MagicMock()
    mock_daemon.notifications_manager.broadcast_state_changed = MagicMock()

    recurring, command, _rule_id = mock_daemon.watchdog_manager._check_recurring_schedules(
        now, 100.0
    )

    assert recurring is False
    assert command is None
    assert mock_daemon.recurring_schedules[0]["last_result"] == "skipped"
    assert mock_daemon.recurring_schedules[0]["skip_next_date"] == ""
    assert created["rule"]["id"] == mock_daemon.recurring_schedules[0]["id"]


def test_recurring_schedule_conflict_is_rejected(mock_daemon):
    start_time = (datetime.now() + timedelta(hours=1)).strftime("%H:%M")
    first = mock_daemon.schedules_manager.cmd_add_recurring_schedule(
        _rule_payload(start_time)
    )
    second = mock_daemon.schedules_manager.cmd_add_recurring_schedule(
        _rule_payload(start_time, name="Conflicting Ritual")
    )

    assert first["status"] == "ok"
    assert second["status"] == "error"
    assert "overlaps" in second["message"]


def test_oneoff_schedule_conflicting_with_recurring_is_rejected(mock_daemon):
    start = datetime.now() + timedelta(days=1, hours=1)
    start = start.replace(second=0, microsecond=0)
    recurring = mock_daemon.schedules_manager.cmd_add_recurring_schedule(
        _rule_payload(
            start.strftime("%H:%M"),
            days_of_week=[start.weekday()],
        )
    )

    response = mock_daemon.session_manager._start_session(
        {
            "action": "start",
            "duration_minutes": 60,
            "mode": "blacklist",
            "session_type": "standard",
            "schedule_at_time": start.strftime("%Y-%m-%dT%H:%M"),
        }
    )

    assert recurring["status"] == "ok"
    assert response["status"] == "error"
    assert "recurring" in response["message"]


def test_duplicate_recurring_schedule_is_created_paused(mock_daemon):
    created = mock_daemon.schedules_manager.cmd_add_recurring_schedule(
        _rule_payload((datetime.now() + timedelta(hours=1)).strftime("%H:%M"))
    )

    duplicate = mock_daemon.schedules_manager.cmd_duplicate_recurring_schedule(
        {"id": created["rule"]["id"]}
    )

    assert duplicate["status"] == "ok"
    assert duplicate["rule"]["enabled"] is False


def test_schedule_cannot_overlap_current_session(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_type = "standard"
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=90)

    response = mock_daemon.session_manager._start_session(
        {
            "action": "start",
            "duration_minutes": 30,
            "mode": "blacklist",
            "session_type": "standard",
            "schedule_in_minutes": 10,
        }
    )

    assert response["status"] == "error"
    assert "active session" in response["message"]


def test_scheduled_execution_does_not_merge_into_current_session(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_type = "standard"
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=90)

    response = mock_daemon.session_manager._start_session(
        {
            "action": "start",
            "duration_minutes": 30,
            "mode": "blacklist",
            "session_type": "standard",
            "scheduled_execution": True,
        }
    )

    assert response["status"] == "error"
    assert "Scheduled session conflicts" in response["message"]
