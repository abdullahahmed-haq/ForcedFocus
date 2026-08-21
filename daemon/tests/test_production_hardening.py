from __future__ import annotations

import json
import logging
import signal
import threading
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import forcefocus_daemon
from forcefocus.api_http import EmbeddedWebHandler
from forcefocus.state_store import StateStore, StateStoreError
from forcefocus.version import STATE_SCHEMA_VERSION


def test_current_schema_refuses_corrupt_state_without_overwriting(tmp_path):
    manifest = {
        "product_version": "1.0.0",
        "schema_version": STATE_SCHEMA_VERSION,
        "files": {"settings.json": STATE_SCHEMA_VERSION},
    }
    StateStore.write_json(tmp_path / "state_manifest.json", manifest)
    corrupt = tmp_path / "settings.json"
    corrupt.write_text("{bad", encoding="utf-8")

    with pytest.raises(StateStoreError, match="settings.json"):
        StateStore(tmp_path).ensure_schema()

    assert corrupt.read_text(encoding="utf-8") == "{bad"


def test_failed_initial_session_persistence_rolls_back_memory(mock_daemon):
    mock_daemon._atomic_write_json = MagicMock(side_effect=OSError("disk full"))

    response = mock_daemon.command_service.dispatch(
        {
            "action": "start",
            "duration_minutes": 30,
            "mode": "blacklist",
            "session_type": "standard",
        }
    )

    assert response["status"] == "error"
    assert response["error_code"] == "SYSTEM_FAILURE"
    assert mock_daemon.state.session.active is False
    assert mock_daemon.state.session.session_expiry is None
    assert mock_daemon.state.active_domains == []


def test_invalid_active_session_is_preserved_for_recovery(mock_daemon):
    session_path = forcefocus_daemon.SESSION_LOCK
    session_path.write_text(json.dumps({"expiry": "not-a-time"}), encoding="utf-8")

    mock_daemon._restore_session()

    assert mock_daemon.recovery_required is True
    assert session_path.exists()
    assert json.loads(session_path.read_text(encoding="utf-8"))["expiry"] == "not-a-time"


def test_stop_rate_limit_uses_daemon_timestamp(mock_daemon):
    mock_daemon.state.session.active = True
    mock_daemon._verify_passphrase = MagicMock(return_value=False)

    for _ in range(6):
        response = mock_daemon.session_manager._request_stop("wrong")

    assert response["status"] == "error"
    assert mock_daemon._passphrase_attempts == 5


def test_signal_handlers_distinguish_reload_from_shutdown(mock_daemon, monkeypatch):
    handlers = {}
    monkeypatch.setattr(
        forcefocus_daemon.signal,
        "signal",
        lambda kind, handler: handlers.__setitem__(kind, handler),
    )

    mock_daemon._install_signal_handlers()
    handlers[signal.SIGHUP](signal.SIGHUP, None)

    assert mock_daemon._reenforce_flag is True
    assert mock_daemon.shutdown_event.is_set() is False

    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert mock_daemon.shutdown_event.is_set() is True


def test_health_contract_exposes_migration_state(mock_daemon):
    mock_daemon.migration_in_progress = True

    response = mock_daemon.command_service.dispatch({"action": "health"})

    assert response["status"] == "ok"
    assert response["migration_in_progress"] is True


def test_state_permission_hardening_clears_stale_macos_user_immutable_flag(
    tmp_path, monkeypatch
):
    """An older install can leave uchg on app-owned state, which must not crash startup."""
    state_file = tmp_path / "ks_hash"
    state_file.write_text('{"hash": "value"}', encoding="utf-8")
    chflags = MagicMock()
    chmod = MagicMock()
    monkeypatch.setattr(forcefocus_daemon.sys, "platform", "darwin")
    monkeypatch.setattr(forcefocus_daemon.subprocess, "run", chflags)
    monkeypatch.setattr(forcefocus_daemon.os, "chmod", chmod)

    forcefocus_daemon.ForcedFocusDaemon._secure_state_file_permissions(state_file)

    chflags.assert_called_once_with(
        ["chflags", "nouchg", str(state_file)],
        capture_output=True,
        check=True,
        text=True,
        timeout=5,
    )
    chmod.assert_called_once_with(str(state_file), 0o600)


def test_disallowed_origin_preflight_is_rejected():
    handler = EmbeddedWebHandler.__new__(EmbeddedWebHandler)
    handler._is_host_allowed = lambda: True
    handler._is_origin_allowed = lambda: False
    handler.send_error = MagicMock()

    handler.do_OPTIONS()

    handler.send_error.assert_called_once_with(403, "Forbidden")


def test_http_security_headers_include_clickjacking_and_sniffing_protection():
    handler = EmbeddedWebHandler.__new__(EmbeddedWebHandler)
    handler.send_header = MagicMock()

    handler._send_security_headers()

    headers = dict(call.args for call in handler.send_header.call_args_list)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_prayer_fetch_failure_does_not_log_coordinates(mock_daemon, monkeypatch, caplog):
    mock_daemon.settings.update(
        {"prayer_latitude": 30.0444, "prayer_longitude": 31.2357}
    )
    monkeypatch.setattr(
        "forcefocus.prayer.urllib.request.urlopen",
        MagicMock(side_effect=OSError("offline")),
    )

    with caplog.at_level(logging.ERROR):
        mock_daemon.prayer_manager._fetch_prayer_calendar(2026, 8)

    assert "30.0444" not in caplog.text
    assert "31.2357" not in caplog.text
    assert "AlAdhan" in caplog.text


def test_slow_prayer_refresh_never_blocks_status_or_watchdog_lock(
    mock_daemon, monkeypatch
):
    """Calendar refresh must not make the local control plane wait on the network."""
    mock_daemon.settings.update(
        {
            "prayer_block_enabled": True,
            "prayer_latitude": 30.0444,
            "prayer_longitude": 31.2357,
        }
    )
    upstream_started = threading.Event()
    release_upstream = threading.Event()

    def slow_failure(*_args, **_kwargs):
        upstream_started.set()
        release_upstream.wait(timeout=1)
        raise TimeoutError("injected slow upstream")

    monkeypatch.setattr(
        "forcefocus.prayer.urllib.request.urlopen",
        slow_failure,
    )

    status_result = []
    status_caller = threading.Thread(
        target=lambda: status_result.append(
            mock_daemon.command_service.dispatch({"action": "status"})
        )
    )
    status_caller.start()
    assert upstream_started.wait(timeout=1)
    status_caller.join(timeout=0.05)
    try:
        assert not status_caller.is_alive(), "status waited for prayer-calendar network I/O"
        assert status_result[0]["status"] == "ok"

        watchdog_caller = threading.Thread(target=mock_daemon.watchdog_manager.watchdog_tick)
        watchdog_caller.start()
        watchdog_caller.join(timeout=0.05)
        assert not watchdog_caller.is_alive(), "watchdog held its global lock during refresh"
    finally:
        release_upstream.set()
        status_caller.join(timeout=1)


def test_failed_prayer_refresh_is_single_flight_and_backed_off(mock_daemon, monkeypatch):
    mock_daemon.settings.update(
        {
            "prayer_block_enabled": True,
            "prayer_latitude": 30.0444,
            "prayer_longitude": 31.2357,
        }
    )
    refresh_finished = threading.Event()
    calls = []

    def fail_once(*_args, **_kwargs):
        calls.append(threading.get_ident())
        refresh_finished.set()
        raise TimeoutError("offline")

    monkeypatch.setattr("forcefocus.prayer.urllib.request.urlopen", fail_once)

    for _ in range(20):
        mock_daemon.session_manager.cmd_get_status()
    assert refresh_finished.wait(timeout=1)
    for _ in range(20):
        mock_daemon.session_manager.cmd_get_status()

    assert len(calls) == 1


def test_prayer_calendar_file_is_loaded_once_per_process(mock_daemon, monkeypatch):
    from forcefocus import prayer

    now = datetime.now()
    prayer.PRAYER_CACHE_FILE.write_text(
        json.dumps(
            {
                f"{now.year}-{now.month:02d}": {
                    f"{now.day:02d}": {
                        "Fajr": "05:00",
                        "Dhuhr": "12:00",
                        "Asr": "15:30",
                        "Maghrib": "18:30",
                        "Isha": "20:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    original_read = mock_daemon.state_store.read_json
    reads = []

    def counted_read(path):
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(mock_daemon.state_store, "read_json", counted_read)

    for _ in range(20):
        mock_daemon.session_manager.cmd_get_status()

    assert reads == [prayer.PRAYER_CACHE_FILE]


def test_prayer_settings_invalidation_discards_an_inflight_old_location_refresh(
    mock_daemon, monkeypatch
):
    from forcefocus import prayer

    now = datetime.now()
    old_fingerprint = (30.0, 31.0, 2)
    new_fingerprint = (40.0, 41.0, 3)
    mock_daemon.settings.update(
        {
            "prayer_latitude": old_fingerprint[0],
            "prayer_longitude": old_fingerprint[1],
            "prayer_method": old_fingerprint[2],
        }
    )
    prayer.PRAYER_CACHE_FILE.write_text(
        json.dumps(
            {
                f"{now.year}-{now.month:02d}": {
                    f"{now.day:02d}": {"Fajr": "05:00"}
                }
            }
        ),
        encoding="utf-8",
    )
    # Load a last-known calendar, then force an A-location refresh to remain
    # in flight while settings switch to location B.
    assert mock_daemon.prayer_manager._get_prayer_times_for_date(now)
    mock_daemon.prayer_manager.invalidate_calendar()
    first_started = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    fingerprints = []

    def calendar(day_time):
        return [
            {
                "date": {"gregorian": {"day": str(now.day)}},
                "timings": {"Fajr": day_time},
            }
        ]

    def fetch(_year, _month, fingerprint=None):
        fingerprints.append(fingerprint)
        if len(fingerprints) == 1:
            first_started.set()
            release_first.wait(timeout=1)
            return calendar("01:00")
        second_finished.set()
        return calendar("02:00")

    monkeypatch.setattr(mock_daemon.prayer_manager, "_fetch_prayer_calendar", fetch)
    mock_daemon.prayer_manager._get_prayer_times_for_date(now)
    assert first_started.wait(timeout=1)

    changed = dict(mock_daemon.settings)
    changed.update(
        {
            "prayer_latitude": new_fingerprint[0],
            "prayer_longitude": new_fingerprint[1],
            "prayer_method": new_fingerprint[2],
        }
    )
    assert mock_daemon.settings_manager.save_settings(changed) is True
    # The old calendar stays available until B succeeds, but B is queued with
    # its own generation/fingerprint and cannot be overwritten by A.
    assert mock_daemon.prayer_manager._get_prayer_times_for_date(now)
    release_first.set()
    assert second_finished.wait(timeout=1)

    deadline = threading.Event()
    for _ in range(100):
        worker = mock_daemon.prayer_manager._refresh_worker_thread
        if worker is None or not worker.is_alive():
            break
        deadline.wait(0.01)
    result = mock_daemon.prayer_manager._get_prayer_times_for_date(now)

    assert fingerprints == [old_fingerprint, new_fingerprint]
    assert result[0]["time"].hour == 2


def test_dns_restore_does_not_restart_sni_proxy(mock_daemon):
    mock_daemon.original_dns = {"Wi-Fi": "1.1.1.1"}
    mock_daemon.enforcement_manager.start_sni_proxy = MagicMock()

    mock_daemon.enforcement_manager._restore_dns()

    mock_daemon.enforcement_manager.start_sni_proxy.assert_not_called()
