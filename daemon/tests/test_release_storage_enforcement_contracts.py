"""Release-critical storage and enforcement behavior at safe adapter seams."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from forcefocus import api_socket
from forcefocus.constants import MARKER_BEGIN, MARKER_END, PERMA_MARKER_BEGIN, PERMA_MARKER_END
from forcefocus.enforcement import EnforcementManager
from forcefocus.migrations import MigrationError, migrate_v0_to_v1, migrate_v1_to_v2
from forcefocus.state_store import StateStore, StateStoreError
from forcefocus.version import STATE_SCHEMA_VERSION


def write_document(path: Path, value) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_legacy_migration_normalizes_all_supported_documents_without_writing(tmp_path):
    write_document(
        tmp_path / "lists.json",
        {"blacklist": [" HTTPS://WWW.Example.COM/path ", "example.com"], "whitelist": ["docs.example"]},
    )
    write_document(tmp_path / "groups.json", {"work": ["*.NEWS.EXAMPLE", "docs.example"]})
    write_document(tmp_path / "settings.json", {"sound_start": "custom.wav"})
    write_document(
        tmp_path / "perma_blocklist.json",
        {"domains": ["WWW.SOCIAL.EXAMPLE"], "pending_unlocks": {"social.example": "2030-01-01T00:00:00+00:00"}},
    )
    write_document(tmp_path / "templates.json", {"templates": [{"id": "one", "name": "Morning"}]})
    write_document(
        tmp_path / "session.lock",
        {"expiry": "2030-01-01T00:00:00+00:00", "schedules": [{}], "recurring_schedules": []},
    )
    write_document(tmp_path / "session_history.json", [{"type": "standard"}])
    write_document(
        tmp_path / "sleep_schedule.json",
        {
            "enabled": True,
            "days_of_week": [4, 0, 4],
            "sleep_time": "22:30",
            "wake_time": "06:45",
            "mode": "blacklist",
            "blacklist": ["WWW.NEWS.EXAMPLE"],
            "whitelist": [],
            "suppressed_occurrences": ["2030-01-01"],
        },
    )

    migrated = migrate_v0_to_v1(tmp_path)

    assert migrated[tmp_path / "lists.json"] == {
        "blacklist": ["example.com"],
        "whitelist": ["docs.example"],
    }
    assert migrated[tmp_path / "groups.json"] == {"work": ["news.example", "docs.example"]}
    assert migrated[tmp_path / "settings.json"]["sound_start"] == "custom.wav"
    assert migrated[tmp_path / "perma_blocklist.json"]["domains"] == ["social.example"]
    assert migrated[tmp_path / "sleep_schedule.json"]["days_of_week"] == [0, 4]
    assert json.loads((tmp_path / "lists.json").read_text())["blacklist"][0].startswith(" HTTPS")


@pytest.mark.parametrize(
    ("filename", "document", "message"),
    [
        ("lists.json", {"blacklist": "example.com"}, "domain collection must be a list"),
        ("groups.json", {"work": ["not a domain"]}, "invalid domain"),
        ("settings.json", {"not_a_setting": True}, "unknown keys"),
        ("perma_blocklist.json", {"domains": [], "pending_unlocks": []}, "must be an object"),
        ("templates.json", {"templates": ["not-an-object"]}, "list of objects"),
        ("session.lock", {"expiry": "tomorrow"}, "ISO timestamp"),
        ("session_history.json", {"history": []}, "list of objects"),
        ("sleep_schedule.json", {"sleep_time": "22:00", "wake_time": "22:00"}, "times must differ"),
    ],
)
def test_legacy_migration_fails_closed_on_ambiguous_or_corrupt_state(tmp_path, filename, document, message):
    write_document(tmp_path / filename, document)

    with pytest.raises(MigrationError, match=message):
        migrate_v0_to_v1(tmp_path)


def test_v2_sleep_migration_clears_untrusted_pending_configuration(tmp_path):
    path = tmp_path / "sleep_schedule.json"
    write_document(
        path,
        {
            "enabled": False,
            "days_of_week": [],
            "sleep_time": "23:00",
            "wake_time": "07:00",
            "mode": "ban",
            "blacklist": [],
            "whitelist": [],
            "suppressed_occurrences": [],
            "pending_config": {"enabled": True},
            "pending_apply_at": "2030-01-01T00:00:00+00:00",
        },
    )

    migrated = migrate_v1_to_v2(tmp_path)[path]

    assert migrated["mode"] == "ban"
    assert migrated["pending_config"] is None
    assert migrated["pending_apply_at"] is None


def test_current_schema_manifest_rejects_unknown_future_schema(tmp_path):
    store = StateStore(tmp_path)
    StateStore.write_json(
        store.manifest_path,
        {"product_version": "future", "schema_version": STATE_SCHEMA_VERSION + 1, "files": {}},
    )

    with pytest.raises(StateStoreError, match="unsupported state schema"):
        store.ensure_schema()


def test_state_store_retains_only_three_newest_recovery_backups(tmp_path):
    store = StateStore(tmp_path)
    store.backups_path.mkdir()
    for name in ["schema-0-20260101T000000Z", "schema-0-20260201T000000Z", "schema-0-20260301T000000Z", "schema-0-20260401T000000Z"]:
        (store.backups_path / name).mkdir()

    store._trim_backups()

    assert [entry.name for entry in sorted(store.backups_path.iterdir())] == [
        "schema-0-20260201T000000Z",
        "schema-0-20260301T000000Z",
        "schema-0-20260401T000000Z",
    ]


def test_state_store_restores_files_and_directories_from_backup(tmp_path):
    store = StateStore(tmp_path)
    backup = tmp_path / "backups" / "schema-1-backup"
    nested = backup / "nested"
    nested.mkdir(parents=True)
    (backup / "lists.json").write_text("backup", encoding="utf-8")
    (nested / "state.txt").write_text("nested backup", encoding="utf-8")
    (tmp_path / "lists.json").write_text("new", encoding="utf-8")
    current_dir = tmp_path / "current"
    current_dir.mkdir()
    (current_dir / "discard.txt").write_text("discard", encoding="utf-8")

    store._restore_backup(backup)

    assert (tmp_path / "lists.json").read_text() == "backup"
    assert (tmp_path / "nested" / "state.txt").read_text() == "nested backup"
    assert not current_dir.exists()


def enforcement_daemon(**overrides):
    session = SimpleNamespace(
        active=True,
        mode="blacklist",
        session_type="standard",
        session_expiry=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    pomodoro = SimpleNamespace(pomo_phase="focus", pomo_next_phase="")
    base = {
        "events": SimpleNamespace(subscribe=lambda *_args: None),
        "state": SimpleNamespace(session=session, pomodoro=pomodoro, active_domains=["news.example"]),
        "perma_blocklist": ["social.example"],
        "prayer_ban_active": "",
        "_ip_backlog": {},
        "_whitelisted_ip_backlog": {},
        "_ip_resolution_running": True,
        "lock": threading.Lock(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_hosts_plan_contains_session_domains_doh_protection_and_expiry():
    manager = EnforcementManager(enforcement_daemon())

    plan = manager._build_blacklist_block()

    assert "# Mode: BLACKLIST" in plan
    assert "# Expires: 2030-01-01T00:00:00+00:00" in plan
    assert "127.0.0.1\tnews.example" in plan
    assert "::1\t\tnews.example" in plan
    assert "# DoH providers (anti-bypass)" in plan


def test_permanent_hosts_plan_expands_common_subdomains_and_ignores_invalid_entries():
    daemon = enforcement_daemon(perma_blocklist=["Example.COM", "invalid", "www.already.example"])
    manager = EnforcementManager(daemon)

    plan = manager._build_perma_block()

    assert "127.0.0.1\texample.com" in plan
    assert "127.0.0.1\twww.example.com" in plan
    assert "127.0.0.1\tinvalid" not in plan
    assert plan.splitlines().count("127.0.0.1\twww.already.example") == 1
    assert plan.splitlines().count("::1\t\twww.already.example") == 1


def test_hosts_marker_stripping_preserves_other_sections():
    content = "\n".join(
        [
            "127.0.0.1 localhost",
            MARKER_BEGIN,
            "127.0.0.1 blocked.example",
            MARKER_END,
            PERMA_MARKER_BEGIN,
            "127.0.0.1 permanent.example",
            PERMA_MARKER_END,
            "10.0.0.1 intranet",
        ]
    )

    without_session = EnforcementManager._strip_block(content)
    without_permanent = EnforcementManager._strip_perma_block(content)

    assert "blocked.example" not in without_session
    assert "permanent.example" in without_session
    assert "permanent.example" not in without_permanent
    assert "blocked.example" in without_permanent
    assert "10.0.0.1 intranet" in without_session


@pytest.mark.parametrize(
    ("mode", "active_domains", "prayer", "domain", "allowed"),
    [
        ("whitelist", ["docs.example"], "", "docs.example", True),
        ("whitelist", ["docs.example"], "", "sub.docs.example", True),
        ("whitelist", ["docs.example"], "", "other.example", False),
        ("blacklist", ["docs.example"], "", "other.example", True),
        ("whitelist", ["docs.example"], "Fajr", "docs.example", False),
        ("whitelist", ["docs.example"], "", "", False),
    ],
)
def test_sni_policy_respects_whitelist_subdomains_and_prayer_priority(mode, active_domains, prayer, domain, allowed):
    daemon = enforcement_daemon(prayer_ban_active=prayer)
    daemon.state.session.mode = mode
    daemon.state.active_domains = active_domains
    manager = EnforcementManager(daemon)

    assert manager._sni_is_allowed(domain) is allowed


class RecordingProcess:
    def __init__(self, command):
        self.command = command
        self.returncode = 0
        self.input = None

    def communicate(self, input=None):
        self.input = input
        return "", ""


@pytest.mark.parametrize(
    ("mode", "prayer", "required_rule"),
    [
        ("blacklist", "", "block return out quick from any to <ff_blocked_ips>"),
        ("whitelist", "", "pass out quick from any to <ff_whitelisted_ips>"),
        ("ban", "", "block return out proto tcp from any to any port 443"),
        ("whitelist", "Fajr", "block return out proto tcp from any to any port 443"),
    ],
)
def test_firewall_plan_matches_active_enforcement_mode(monkeypatch, mode, prayer, required_rule):
    daemon = enforcement_daemon(prayer_ban_active=prayer)
    daemon.state.session.mode = mode
    manager = EnforcementManager(daemon)
    processes = []
    run_commands = []
    monkeypatch.setattr(
        "forcefocus.enforcement.firewall.subprocess.Popen",
        lambda command, **_kwargs: processes.append(RecordingProcess(command)) or processes[-1],
    )
    monkeypatch.setattr(
        "forcefocus.enforcement.firewall.subprocess.run",
        lambda command, **_kwargs: run_commands.append(command) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        "forcefocus.enforcement.firewall.threading.Thread",
        lambda **_kwargs: SimpleNamespace(start=lambda: None),
    )

    manager._enforce_firewall(True, upstream_dns="1.1.1.1")

    assert processes[0].command == ["pfctl", "-a", "forcefocus", "-f", "-"]
    assert required_rule in processes[0].input
    assert "to 1.1.1.1 port 53" in processes[0].input
    assert ["pfctl", "-E"] in run_commands
    assert ["pfctl", "-k", "0.0.0.0/0", "-k", "443"] in run_commands


def test_firewall_disable_only_clears_the_forcefocus_anchor(monkeypatch):
    manager = EnforcementManager(enforcement_daemon())
    commands = []
    monkeypatch.setattr(
        "forcefocus.enforcement.firewall.subprocess.run",
        lambda command, **_kwargs: commands.append(command),
    )

    manager._enforce_firewall(False)

    assert commands == [["pfctl", "-a", "forcefocus", "-F", "all"]]


def test_ip_table_refresh_resolves_session_and_permanent_domains_without_system_changes(monkeypatch):
    daemon = enforcement_daemon()
    manager = EnforcementManager(daemon)
    processes = []
    address_map = {
        "news.example": "203.0.113.10",
        "social.example": "203.0.113.20",
    }
    monkeypatch.setattr(
        "forcefocus.enforcement.firewall.socket.getaddrinfo",
        lambda domain, *_args: [(None, None, None, None, (address_map[domain], 0))],
    )
    monkeypatch.setattr(
        "forcefocus.enforcement.firewall.subprocess.Popen",
        lambda command, **_kwargs: processes.append(RecordingProcess(command)) or processes[-1],
    )

    manager._update_blocked_ips()

    by_table = {process.command[4]: process.input for process in processes}
    assert set(by_table["ff_blocked_ips"].splitlines()) == {"203.0.113.10", "203.0.113.20"}
    assert by_table["ff_whitelisted_ips"] == ""
    assert daemon._ip_resolution_running is False


class FakeConnection:
    def __init__(self, daemon, chunks):
        self.daemon = daemon
        self.chunks = iter(chunks)
        self.sent = []
        self.timeout = None
        self.closed = False

    def settimeout(self, seconds):
        self.timeout = seconds

    def recv(self, _size):
        return next(self.chunks, b"")

    def sendall(self, value):
        self.sent.append(json.loads(value))
        self.daemon.shutdown_event.set()

    def close(self):
        self.closed = True


class FakeListeningSocket:
    def __init__(self, connection):
        self.connection = connection
        self.bound = None
        self.listen_backlog = None
        self.timeout = None
        self.closed = False

    def bind(self, path):
        self.bound = path

    def listen(self, backlog):
        self.listen_backlog = backlog

    def settimeout(self, timeout):
        self.timeout = timeout

    def accept(self):
        return self.connection, None

    def close(self):
        self.closed = True


def test_socket_server_reads_until_eof_and_returns_command_response(monkeypatch, tmp_path):
    event = threading.Event()
    service = SimpleNamespace(dispatch=lambda command: {"status": "ok", "action": command["action"]})
    daemon = SimpleNamespace(shutdown_event=event, command_service=service)
    connection = FakeConnection(daemon, [b'{"action":', b'"health"}', b""])
    listener = FakeListeningSocket(connection)
    socket_path = tmp_path / "forcefocus.sock"
    unlinked = []
    monkeypatch.setattr(api_socket, "SOCK_PATH", str(socket_path))
    monkeypatch.setattr(api_socket.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(api_socket.os, "chmod", lambda path, mode: (path, mode))
    monkeypatch.setattr(api_socket.os, "unlink", lambda path: unlinked.append(path))
    monkeypatch.setattr(api_socket.socket, "socket", lambda *_args: listener)

    api_socket.SocketAPIManager(daemon).socket_server()

    assert listener.bound == str(socket_path)
    assert listener.listen_backlog == 5
    assert connection.timeout == 5.0
    assert connection.sent == [{"status": "ok", "action": "health"}]
    assert connection.closed is True
    assert listener.closed is True
    assert unlinked == [str(socket_path)]


def test_socket_server_rejects_oversized_documents_before_dispatch(monkeypatch, tmp_path):
    event = threading.Event()

    def unexpected_dispatch(_command):
        pytest.fail("oversized document must not reach command dispatch")

    daemon = SimpleNamespace(shutdown_event=event, command_service=SimpleNamespace(dispatch=unexpected_dispatch))
    connection = FakeConnection(daemon, [b"x" * (1024 * 1024 + 1)])
    listener = FakeListeningSocket(connection)
    monkeypatch.setattr(api_socket, "SOCK_PATH", str(tmp_path / "forcefocus.sock"))
    monkeypatch.setattr(api_socket.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(api_socket.os, "chmod", lambda *_args: None)
    monkeypatch.setattr(api_socket.os, "unlink", lambda _path: None)
    monkeypatch.setattr(api_socket.socket, "socket", lambda *_args: listener)

    api_socket.SocketAPIManager(daemon).socket_server()

    assert connection.sent == [{
        "status": "error",
        "error_code": "INVALID_INPUT",
        "message": "Message too large.",
    }]
    assert connection.closed is True
