import pytest
from datetime import datetime, timedelta
import forcefocus_daemon
import json
from forcefocus.utils import get_continuous_time

def test_record_session_history(mock_daemon):
    # Set up session state
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_type = "standard"
    mock_daemon.state.session.intent = "Testing"
    mock_daemon.state.session.total_duration_seconds = 3600
    mock_daemon._mono_session_end = 0.0 # Will be completed
    mock_daemon.state.session.session_expiry = datetime.now()
    
    mock_daemon.history_manager.record_session_history()
    
    history_file = forcefocus_daemon.HISTORY_FILE
    assert history_file.exists()
    
    data = json.loads(history_file.read_text())
    assert len(data) == 1
    assert data[0]["intent"] == "Testing"
    assert data[0]["duration_minutes"] == 60


def test_history_uses_elapsed_time_when_session_ends_early(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_type = "standard"
    mock_daemon.state.session.total_duration_seconds = 60 * 60
    mock_daemon._mono_session_end = get_continuous_time() + (30 * 60)
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=30)

    mock_daemon.history_manager.record_session_history()

    data = json.loads(forcefocus_daemon.HISTORY_FILE.read_text())
    assert data[0]["duration_minutes"] == 30
    assert data[0]["net_focus_minutes"] == 30


def test_rescue_and_prayer_events_do_not_count_toward_focus_goal(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "ban"
    mock_daemon.state.session.session_type = "rescue"
    mock_daemon.state.session.total_duration_seconds = 15 * 60
    mock_daemon._mono_session_end = get_continuous_time()
    mock_daemon.state.session.session_expiry = datetime.now()

    mock_daemon.history_manager.record_session_history()
    mock_daemon.history_manager.record_prayer_event("Fajr", "started")
    result = mock_daemon.history_manager.cmd_get_session_history({"range": "today"})

    assert result["summary"]["net_focus_minutes"] == 0
    assert result["summary"]["rescue_minutes"] == 15
    assert result["summary"]["daily_totals"][datetime.now().strftime("%Y-%m-%d")]["minutes"] == 0
    assert len(result["events"]) == 1


def test_partial_pomodoro_phase_records_only_elapsed_focus(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "pomodoro"
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.total_duration_seconds = 30 * 60
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=25)
    mock_daemon.state.pomodoro.pomo_phase = "focus"
    mock_daemon.state.pomodoro.pomo_focus_minutes = 25
    mock_daemon.state.pomodoro.pomo_break_minutes = 5
    mock_daemon._mono_pomo_phase_end = get_continuous_time() + (5 * 60)

    mock_daemon.history_manager.record_session_history()

    data = json.loads(forcefocus_daemon.HISTORY_FILE.read_text())
    assert data[0]["duration_minutes"] == 20
    assert data[0]["net_focus_minutes"] == 20
