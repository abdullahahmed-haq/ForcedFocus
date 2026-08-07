import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

def test_validate_settings_preserves_defaults(mock_daemon):
    # Pass empty dict
    valid_flag, msg, valid_dict = mock_daemon._validate_settings({})
    
    assert valid_flag is True
    # Check that defaults are merged
    assert "sound_start" in valid_dict
    assert "intent_notification_enabled" in valid_dict
    assert valid_dict["prayer_block_enabled"] is False

def test_validate_settings_type_rejection(mock_daemon):
    valid_flag, msg, valid_dict = mock_daemon._validate_settings({
        "prayer_latitude": "34.05",  # String instead of float
    })
    assert valid_flag is False
    assert "must be a number" in msg

    valid_flag, msg, valid_dict = mock_daemon._validate_settings({
        "intent_notification_interval": "30" # String instead of int
    })
    assert valid_flag is False
    assert "must be an integer" in msg

def test_validate_settings_bounds(mock_daemon):
    valid_flag, msg, valid_dict = mock_daemon._validate_settings({
        "intent_notification_interval": -1 # Too small
    })
    
    assert valid_flag is False
    assert "must be positive" in msg


def test_validate_prayer_settings_bounds(mock_daemon):
    valid_flag, msg, _ = mock_daemon._validate_settings({
        "prayer_latitude": 91,
    })
    assert valid_flag is False
    assert "between -90 and 90" in msg

    valid_flag, msg, _ = mock_daemon._validate_settings({
        "prayer_minutes_after": -1,
    })
    assert valid_flag is False
    assert "zero or greater" in msg


def test_prayer_mode_cannot_be_disabled_while_ban_is_active(mock_daemon):
    mock_daemon.settings["prayer_block_enabled"] = True
    mock_daemon.prayer_ban_active = "Isha"

    response = mock_daemon.settings_manager.cmd_save_settings({
        "settings": {"prayer_block_enabled": False},
    })

    assert response["status"] == "error"
    assert "Prayer Ban is active" in response["message"]


def test_prayer_mode_cannot_be_disabled_near_block_start(mock_daemon):
    mock_daemon.settings.update(
        {
            "prayer_block_enabled": True,
            "prayer_minutes_before": 10,
            "prayer_skipped": {},
        }
    )
    mock_daemon.prayer_manager._upcoming_prayers = MagicMock(
        return_value=[{"name": "Asr", "time": datetime.now() + timedelta(minutes=35)}]
    )

    response = mock_daemon.settings_manager.cmd_save_settings(
        {"settings": {"prayer_block_enabled": False}}
    )

    assert response["status"] == "error"
    assert "within 30 minutes of a Prayer block" in response["message"]
