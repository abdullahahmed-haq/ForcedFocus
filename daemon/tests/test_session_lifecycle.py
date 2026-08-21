import pytest
import forcefocus_daemon
from datetime import datetime, timedelta
from unittest.mock import MagicMock

def test_start_session_blacklist(mock_daemon):
    # Setup domains to block by saving them so _load_lists finds them
    mock_daemon.domains_manager.save_lists({"blacklist": ["example.com", "test.com"], "whitelist": []})
    
    # Start session
    response = mock_daemon.session_manager._start_session({
        "action": "start",
        "duration_minutes": 60,
        "mode": "blacklist",
        "type": "standard",
        "intent": "Focus on work"
    })
    
    assert response["status"] == "ok"
    assert mock_daemon.state.session.active is True
    assert mock_daemon.state.session.mode == "blacklist"
    assert "example.com" in mock_daemon.session_base_domains
    assert "test.com" in mock_daemon.session_base_domains


def test_start_session_with_empty_blacklist_has_no_implicit_defaults(mock_daemon):
    mock_daemon.domains_manager.save_lists({"blacklist": [], "whitelist": []})

    response = mock_daemon.session_manager._start_session({
        "action": "start",
        "duration_minutes": 60,
        "mode": "blacklist",
        "session_type": "standard",
    })

    assert response["status"] == "ok"
    assert mock_daemon.session_base_domains == []
    assert mock_daemon.state.active_domains == []


def test_empty_blacklist_enforcement_preserves_only_permanent_rules(mock_daemon):
    from unittest.mock import MagicMock

    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.active_domains = []
    mock_daemon.enforcement_manager._enforce_perma_block = MagicMock()
    mock_daemon.enforcement_manager._enforce_browser_policies = MagicMock()
    mock_daemon.enforcement_manager._enforce_block = MagicMock()

    mock_daemon.enforcement_manager._enforce_current_mode()

    mock_daemon.enforcement_manager._enforce_perma_block.assert_called_once()
    mock_daemon.enforcement_manager._enforce_browser_policies.assert_called_once_with(False)
    mock_daemon.enforcement_manager._enforce_block.assert_not_called()

def test_start_session_while_active_merges(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_type = "standard"
    from datetime import datetime, timedelta
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=10)
    
    response = mock_daemon.session_manager._start_session({
        "duration_minutes": 60,
        "mode": "blacklist",
        "type": "standard",
        "session_type": "standard"
    })
    
    # Overlapping sessions with the same mode and type merge successfully
    assert response["status"] == "ok"

def test_request_stop_delayed(mock_daemon, monkeypatch):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.pending_unlock_at = None
    mock_daemon.state.session.mode = "blacklist"
    
    import forcefocus_daemon
    mock_daemon.KS_HASH_FILE = forcefocus_daemon.KS_HASH_FILE
    
    response = mock_daemon.session_manager._request_stop("")
    
    assert response["status"] == "delayed" or response["status"] == "error"

def test_cleanup_session_resets_state(mock_daemon):
    from datetime import datetime
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_expiry = datetime.now()
    mock_daemon.state.session.intent = "To be deleted"
    
    mock_daemon.session_manager._cleanup_session()
    
    assert mock_daemon.state.session.active is False
    assert mock_daemon.state.session.session_expiry is None
    assert mock_daemon.state.session.intent is None


def test_invalid_pomodoro_input_does_not_partially_activate_session(mock_daemon):
    response = mock_daemon.command_service.dispatch({
        "action": "start",
        "duration_minutes": 60,
        "mode": "blacklist",
        "session_type": "pomodoro",
        "focus_minutes": "not-a-number",
        "break_minutes": 5,
        "cycles": 4,
    })

    assert response["status"] == "error"
    assert response["error_code"] == "INVALID_INPUT"
    assert mock_daemon.state.session.active is False
    assert mock_daemon.state.session.session_expiry is None


def test_cleanup_preserves_future_oneoff_schedules(mock_daemon):
    start = datetime.now() + timedelta(hours=2)
    mock_daemon.schedules = [{
        "start_time": start,
        "end_time": start + timedelta(minutes=30),
        "mono_start": 100.0,
        "cmd": {
            "duration_minutes": 30,
            "mode": "blacklist",
            "session_type": "standard",
        },
    }]
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "blacklist"
    mock_daemon.state.session.session_expiry = datetime.now()
    mock_daemon.state.session.total_duration_seconds = 60
    mock_daemon.enforcement_manager._on_session_ended = MagicMock()

    mock_daemon.session_manager._cleanup_session()

    assert forcefocus_daemon.SESSION_LOCK.exists()
    assert mock_daemon.schedules[0]["start_time"] == start
