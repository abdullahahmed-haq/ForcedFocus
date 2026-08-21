from datetime import datetime, timedelta
from unittest.mock import MagicMock


def test_prayer_suspends_and_restores_regular_session_remainder(mock_daemon, monkeypatch):
    import forcefocus.watchdog as watchdog

    clock = [100.0]
    monkeypatch.setattr(watchdog, "get_continuous_time", lambda: clock[0])
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "whitelist"
    mock_daemon.state.session.session_type = "pomodoro"
    mock_daemon.state.session.total_duration_seconds = 600
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(seconds=600)
    mock_daemon.state.pomodoro.pomo_phase = "focus"
    mock_daemon._mono_session_end = 700.0
    mock_daemon._mono_pomo_phase_end = 400.0
    mock_daemon.enforcement_manager.start_sni_proxy = MagicMock()
    mock_daemon.enforcement_manager._enforce_firewall = MagicMock()
    mock_daemon.enforcement_manager._enforce_browser_policies = MagicMock()
    mock_daemon.enforcement_manager._kill_vpn_interfaces = MagicMock()
    mock_daemon.enforcement_manager._kill_restricted_apps = MagicMock()
    mock_daemon.enforcement_manager._flush_dns = MagicMock()
    mock_daemon.enforcement_manager._enforce_current_mode = MagicMock()
    mock_daemon.notifications_manager.play_sound = MagicMock()
    mock_daemon.notifications_manager.broadcast_state_changed = MagicMock()

    mock_daemon.prayer_manager._evaluate_prayer_block = MagicMock(
        return_value=(True, "Asr")
    )
    mock_daemon.watchdog_manager._check_prayer_blocks(datetime.now())

    assert mock_daemon.prayer_suspension["session_remaining_seconds"] == 600.0
    assert mock_daemon.prayer_suspension["pomo_phase_remaining_seconds"] == 300.0

    clock[0] = 250.0
    mock_daemon.prayer_manager._evaluate_prayer_block.return_value = (False, "")
    mock_daemon.watchdog_manager._check_prayer_blocks(datetime.now())

    assert mock_daemon.prayer_suspension is None
    assert mock_daemon._mono_session_end == 850.0
    assert mock_daemon._mono_pomo_phase_end == 550.0
    mock_daemon.enforcement_manager._enforce_current_mode.assert_called_once()


def test_prayer_firewall_has_no_whitelist_bypass(mock_daemon, monkeypatch):
    import forcefocus.enforcement.firewall as firewall

    popen = MagicMock()
    popen.return_value.communicate.return_value = ("", "")
    popen.return_value.returncode = 0
    monkeypatch.setattr(firewall.subprocess, "Popen", popen)
    thread = MagicMock()
    monkeypatch.setattr(firewall.threading, "Thread", thread)
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "whitelist"
    mock_daemon.state.active_domains = ["allowed.example"]
    mock_daemon.prayer_ban_active = "Asr"

    mock_daemon.enforcement_manager._enforce_firewall(True)

    rules = popen.return_value.communicate.call_args.kwargs["input"]
    assert "pass out quick from any to <ff_whitelisted_ips>" not in rules
    assert "block return out proto tcp from any to any port 443" in rules
    assert "block return out proto udp from any to any port 443" in rules
    assert not mock_daemon.enforcement_manager._sni_is_allowed("allowed.example")


def test_ban_firewall_does_not_pass_stale_whitelist_ips(mock_daemon, monkeypatch):
    import forcefocus.enforcement.firewall as firewall

    popen = MagicMock()
    popen.return_value.communicate.return_value = ("", "")
    popen.return_value.returncode = 0
    monkeypatch.setattr(firewall.subprocess, "Popen", popen)
    monkeypatch.setattr(firewall.threading, "Thread", MagicMock())
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "ban"

    mock_daemon.enforcement_manager._enforce_firewall(True)

    rules = popen.return_value.communicate.call_args.kwargs["input"]
    assert "pass out quick from any to <ff_whitelisted_ips>" not in rules
    assert "block return out proto udp from any to any port 443" in rules


def test_whitelist_captures_dns_before_redirecting(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.mode = "whitelist"
    mock_daemon.enforcement_manager._get_current_dns_servers = MagicMock(
        return_value={"Wi-Fi": "1.1.1.1"}
    )
    mock_daemon._persist_session_lock = MagicMock()
    mock_daemon.enforcement_manager.start_dns_proxy = MagicMock()
    mock_daemon.enforcement_manager.start_sni_proxy = MagicMock()
    mock_daemon.enforcement_manager._set_dns_to_localhost = MagicMock()
    mock_daemon.enforcement_manager._enforce_doh_block = MagicMock()
    mock_daemon.enforcement_manager._enforce_firewall = MagicMock()
    mock_daemon.enforcement_manager._enforce_browser_policies = MagicMock()
    mock_daemon.enforcement_manager._reset_system_proxies = MagicMock()
    mock_daemon.enforcement_manager._kill_vpn_interfaces = MagicMock()
    mock_daemon.enforcement_manager._kill_restricted_apps = MagicMock()
    mock_daemon.enforcement_manager._clear_browser_caches = MagicMock()
    mock_daemon.enforcement_manager._flush_dns = MagicMock()

    mock_daemon.enforcement_manager._enforce_whitelist()

    assert mock_daemon.original_dns == {"Wi-Fi": "1.1.1.1"}
    mock_daemon._persist_session_lock.assert_called_once()
    mock_daemon.enforcement_manager._set_dns_to_localhost.assert_called_once()


def test_prayer_window_continues_after_midnight(mock_daemon, monkeypatch):
    from datetime import datetime

    after_midnight = datetime(2026, 1, 2, 0, 10)
    previous_day = after_midnight - timedelta(days=1)
    mock_daemon.settings.update(
        {
            "prayer_block_enabled": True,
            "prayer_minutes_before": 10,
            "prayer_minutes_after": 30,
            "prayer_skipped": {},
        }
    )

    def prayer_times(day):
        if day.date() == previous_day.date():
            return [{"name": "Isha", "time": day.replace(hour=23, minute=50, second=0, microsecond=0)}]
        return []

    monkeypatch.setattr(
        mock_daemon.prayer_manager, "_get_prayer_times_for_date", prayer_times
    )

    active, prayer_name = mock_daemon.prayer_manager._evaluate_prayer_block(
        after_midnight
    )

    assert active is True
    assert prayer_name == "Isha"


def test_prayer_skip_broadcasts_state_change(mock_daemon, monkeypatch):
    upcoming = datetime.now() + timedelta(minutes=31)
    mock_daemon.settings["prayer_block_enabled"] = True
    mock_daemon.settings["prayer_skipped"] = {}
    mock_daemon.prayer_manager._upcoming_prayers = MagicMock(
        return_value=[{"name": "Fajr", "time": upcoming}]
    )
    mock_daemon.settings_manager.save_settings = MagicMock(return_value=True)
    mock_daemon.notifications_manager.broadcast_state_changed = MagicMock()

    response = mock_daemon.prayer_manager.cmd_skip_prayer({"prayer_name": "Fajr"})

    assert response["status"] == "ok"
    mock_daemon.notifications_manager.broadcast_state_changed.assert_called_once()


def test_restored_suspension_resumes_when_prayer_window_has_ended(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon.state.session.session_type = "standard"
    mock_daemon.state.session.session_expiry = datetime.now() + timedelta(minutes=10)
    mock_daemon._mono_session_end = 700.0
    mock_daemon.prayer_suspension = {
        "session_remaining_seconds": 600.0,
        "suspended_at": (datetime.now() - timedelta(minutes=30)).isoformat(),
    }
    mock_daemon.prayer_manager.active_prayer_window = MagicMock(return_value=None)
    mock_daemon._persist_session_lock = MagicMock()

    mock_daemon.watchdog_manager.restore_prayer_suspension(100.0, datetime.now())

    assert mock_daemon.prayer_suspension is None
    assert mock_daemon._mono_session_end == 700.0


def test_permanent_unblock_expiration_persists_and_reenforces(mock_daemon):
    mock_daemon.perma_blocklist = ["expired.example"]
    mock_daemon.perma_pending_unlocks = {
        "expired.example": datetime.now() - timedelta(seconds=1)
    }
    mock_daemon._mono_perma_unlock_ends = {"expired.example": 10.0}
    mock_daemon.domains_manager._save_perma_state = MagicMock()
    mock_daemon.enforcement_manager._enforce_perma_block = MagicMock()
    mock_daemon.notifications_manager.broadcast_state_changed = MagicMock()

    mock_daemon.watchdog_manager._check_perma_blocklist(11.0)

    assert mock_daemon.perma_blocklist == []
    mock_daemon.domains_manager._save_perma_state.assert_called_once()
    mock_daemon.enforcement_manager._enforce_perma_block.assert_called_once()
