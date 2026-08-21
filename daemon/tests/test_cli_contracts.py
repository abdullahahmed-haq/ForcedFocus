"""CLI contracts exercised with the daemon socket and terminal as boundaries."""

from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from cli import client
from cli.commands import domains, groups, perma_block, schedule, settings, sound, start, status, stop, templates, web


class FakeOut:
    def __init__(self, *, human: bool = False):
        self.is_human = human
        self.is_agent = not human
        self.data: list[tuple[dict, str | None]] = []

    def print_data(self, data, title=None):
        self.data.append((data, title))

    def print_error(self, message, code="ERROR", suggestion=None):
        raise CLIError(code, message, suggestion)


class CLIError(Exception):
    def __init__(self, code, message, suggestion=None):
        super().__init__(message)
        self.code = code
        self.suggestion = suggestion


class FakeConsole:
    def __init__(self):
        self.renderables = []

    def print(self, *values):
        self.renderables.append(values)

    def status(self, _message):
        return nullcontext()


def command_harness(monkeypatch, module, responses=None, *, human=False):
    sent = []
    queued = list(responses or [{"status": "ok"}])

    def send(payload):
        sent.append(payload)
        return queued.pop(0) if queued else {"status": "ok"}

    fake_out = FakeOut(human=human)
    fake_console = FakeConsole()
    monkeypatch.setattr(module, "send_command", send, raising=False)
    monkeypatch.setattr(module, "out", fake_out)
    if hasattr(module, "console"):
        monkeypatch.setattr(module, "console", fake_console)
    return sent, fake_out, fake_console


class FakeSocket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.connected_to = None
        self.timeout = None
        self.sent = b""
        self.shutdown_mode = None
        self.closed = False

    def settimeout(self, seconds):
        self.timeout = seconds

    def connect(self, path):
        self.connected_to = path

    def sendall(self, payload):
        self.sent += payload

    def shutdown(self, mode):
        self.shutdown_mode = mode

    def recv(self, _size):
        return next(self.chunks, b"")

    def close(self):
        self.closed = True


def test_client_sends_one_json_document_and_reassembles_chunked_response(monkeypatch):
    sock = FakeSocket([b'{"status":', b'"ok","active":false}'])
    fake_socket_module = SimpleNamespace(
        AF_UNIX=1,
        SOCK_STREAM=2,
        SHUT_WR=3,
        timeout=TimeoutError,
        socket=lambda *_args: sock,
    )
    monkeypatch.setattr(client.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(client, "socket", fake_socket_module)

    response = client.send_command({"action": "status"})

    assert response == {"status": "ok", "active": False}
    assert json.loads(sock.sent) == {"action": "status"}
    assert sock.connected_to == client.SOCK_PATH
    assert sock.timeout == 10
    assert sock.shutdown_mode == fake_socket_module.SHUT_WR
    assert sock.closed is True


def test_client_reports_missing_socket_as_a_stable_error(monkeypatch):
    fake_out = FakeOut()
    monkeypatch.setattr(client.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(client, "out", fake_out)

    with pytest.raises(CLIError) as exc_info:
        client.send_command({"action": "status"})

    assert exc_info.value.code == "DAEMON_NOT_FOUND"
    assert "sudo launchctl" in exc_info.value.suggestion


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (SimpleNamespace(action="add", list="blacklist", domains=["news.example"]), {"action": "add_domain", "list": "blacklist", "domain": "news.example"}),
        (SimpleNamespace(action="add", list="whitelist", domains=["one.example", "two.example"]), {"action": "add_domains", "list": "whitelist", "domains": ["one.example", "two.example"]}),
        (SimpleNamespace(action="remove", list="blacklist", domain="news.example"), {"action": "remove_domain", "list": "blacklist", "domain": "news.example"}),
    ],
)
def test_domains_commands_emit_exact_daemon_documents(monkeypatch, args, expected):
    sent, fake_out, _console = command_harness(monkeypatch, domains)

    domains.cmd_domains(args)

    assert sent == [expected]
    assert fake_out.data[-1][0]["status"] == "ok"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (SimpleNamespace(action="add", name="work", domains=["docs.example"]), {"action": "add_group", "name": "work", "domains": ["docs.example"]}),
        (SimpleNamespace(action="remove", name="work", domains=None), {"action": "remove_group", "name": "work"}),
    ],
)
def test_group_mutations_emit_exact_daemon_documents(monkeypatch, args, expected):
    sent, fake_out, _console = command_harness(monkeypatch, groups)

    groups.cmd_groups(args)

    assert sent == [expected]
    assert fake_out.data[-1][0] == {"status": "ok"}


def test_agent_list_commands_preserve_daemon_documents(monkeypatch):
    cases = [
        (domains, domains.cmd_domains, SimpleNamespace(action="show"), {"status": "ok", "lists": {"blacklist": ["a.example"], "whitelist": []}}, {"action": "get_lists"}),
        (groups, groups.cmd_groups, SimpleNamespace(action="list", name=None), {"status": "ok", "groups": {"work": ["docs.example"]}}, {"action": "get_groups"}),
        (perma_block, perma_block.cmd_perma_block, SimpleNamespace(action="list"), {"status": "ok", "domains": ["social.example"]}, {"action": "get_perma_blocklist"}),
        (sound, sound.cmd_sound, SimpleNamespace(action="list"), {"status": "ok", "sounds": ["bell.wav"]}, {"action": "get_sounds"}),
    ]
    for module, command, args, response, expected in cases:
        sent, fake_out, _console = command_harness(monkeypatch, module, [response])
        command(args)
        assert sent == [expected]
        assert fake_out.data == [(response, None)]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (SimpleNamespace(action="add", domains=["social.example"], domain=None, key=None), {"action": "add_perma_block", "domains": ["social.example"]}),
        (SimpleNamespace(action="unblock", domains=None, domain="social.example", key="secret"), {"action": "request_perma_unblock", "domain": "social.example", "key": "secret"}),
        (SimpleNamespace(action="cancel", domains=None, domain="social.example", key=None), {"action": "cancel_perma_unblock", "domain": "social.example"}),
    ],
)
def test_permanent_block_commands_keep_unlock_credentials_scoped(monkeypatch, args, expected):
    sent, fake_out, _console = command_harness(monkeypatch, perma_block)

    perma_block.cmd_perma_block(args)

    assert sent == [expected]
    assert fake_out.data[-1][0] == {"status": "ok"}


def test_start_pomodoro_calculates_total_duration_and_prefers_relative_schedule(monkeypatch):
    sent, fake_out, _console = command_harness(monkeypatch, start)
    args = SimpleNamespace(
        mode="blacklist",
        session_type="pomodoro",
        duration=1,
        focus=25,
        break_time=5,
        cycles=4,
        schedule_in=15,
        schedule_at="09:00",
        groups=["work"],
    )

    start.cmd_start(args)

    assert sent == [{
        "action": "start",
        "duration_minutes": 120,
        "mode": "blacklist",
        "session_type": "pomodoro",
        "focus_minutes": 25,
        "break_minutes": 5,
        "cycles": 4,
        "schedule_in_minutes": 15,
        "groups": ["work"],
    }]
    assert fake_out.data[-1][1] == "Start Session"


def test_stop_agent_mode_requires_key_without_prompting(monkeypatch):
    _sent, _fake_out, _console = command_harness(monkeypatch, stop)
    monkeypatch.setattr(stop.getpass, "getpass", lambda _prompt: pytest.fail("must not prompt"))

    with pytest.raises(CLIError) as exc_info:
        stop.cmd_stop(SimpleNamespace(key=None))

    assert exc_info.value.code == "MISSING_KEY"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("YES", True), ("off", False), ("45", 45)],
)
def test_settings_cli_validates_types_and_merges_existing_values(monkeypatch, raw_value, expected):
    key = "intent_notification_interval" if isinstance(expected, int) and not isinstance(expected, bool) else "intent_notification_enabled"
    responses = [
        {"status": "ok", "settings": {"sound_start": "existing.wav"}},
        {"status": "ok", "message": "saved"},
    ]
    sent, fake_out, _console = command_harness(monkeypatch, settings, responses)

    settings.cmd_settings(SimpleNamespace(action="set", key=key, value=raw_value))

    assert sent[0] == {"action": "get_settings"}
    assert sent[1] == {
        "action": "save_settings",
        "settings": {"sound_start": "existing.wav", key: expected},
    }
    assert fake_out.data[-1][0]["message"] == "saved"


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("unknown", "value", "INVALID_KEY"),
        ("intent_notification_enabled", "sometimes", "INVALID_VALUE"),
        ("intent_notification_interval", "soon", "INVALID_VALUE"),
    ],
)
def test_settings_cli_rejects_values_before_saving(monkeypatch, key, value, code):
    sent, _fake_out, _console = command_harness(
        monkeypatch,
        settings,
        [{"status": "ok", "settings": {}}],
    )

    with pytest.raises(CLIError) as exc_info:
        settings.cmd_settings(SimpleNamespace(action="set", key=key, value=value))

    assert exc_info.value.code == code
    assert sent == [{"action": "get_settings"}]


def test_recurring_pomodoro_schedule_emits_complete_rule(monkeypatch):
    sent, fake_out, _console = command_harness(monkeypatch, schedule)
    args = SimpleNamespace(
        action="add",
        recurring=True,
        days="0,2,4",
        time="09:30",
        duration=1,
        mode="whitelist",
        session_type="pomodoro",
        focus=50,
        break_time=10,
        cycles=3,
        groups=["work"],
        name="Writing",
    )

    schedule.cmd_schedule(args)

    assert sent == [{
        "action": "add_recurring_schedule",
        "name": "Writing",
        "days_of_week": [0, 2, 4],
        "start_time": "09:30",
        "duration_minutes": 180,
        "mode": "whitelist",
        "session_type": "pomodoro",
        "groups": ["work"],
        "focus_minutes": 50,
        "break_minutes": 10,
        "cycles": 3,
    }]
    assert fake_out.data[-1][1] == "Add Recurring Schedule"


@pytest.mark.parametrize(
    ("action", "expected_action"),
    [("remove", "remove_recurring_schedule"), ("pause", "pause_recurring_schedule"), ("resume", "resume_recurring_schedule")],
)
def test_schedule_lifecycle_commands_target_rule_id(monkeypatch, action, expected_action):
    sent, _fake_out, _console = command_harness(monkeypatch, schedule)
    args = SimpleNamespace(action=action, id="rule-1")

    schedule.cmd_schedule(args)

    assert sent == [{"action": expected_action, "id": "rule-1"}]


def test_schedule_edit_sends_only_fields_the_user_changed(monkeypatch):
    sent, _fake_out, _console = command_harness(monkeypatch, schedule)
    args = SimpleNamespace(
        action="edit",
        id="rule-1",
        name="Deep work",
        days="1,3",
        time=None,
        duration=90,
        mode=None,
        session_type=None,
        focus=None,
        break_time=None,
        cycles=None,
        groups=None,
        enabled="false",
    )

    schedule.cmd_schedule(args)

    assert sent == [{
        "action": "update_recurring_schedule",
        "id": "rule-1",
        "name": "Deep work",
        "days_of_week": [1, 3],
        "duration_minutes": 90,
        "enabled": False,
    }]


def test_template_name_resolution_is_case_insensitive_and_prefixes_must_be_unique():
    listing = [
        {"id": "abc123", "name": "Morning Focus"},
        {"id": "abd456", "name": "Evening"},
    ]

    assert templates._find_template("morning focus", listing)["id"] == "abc123"
    assert templates._find_template("abc", listing)["name"] == "Morning Focus"
    assert templates._find_template("ab", listing) is None


def test_template_start_resolves_human_reference_before_mutation(monkeypatch):
    responses = [
        {"status": "ok", "templates": [{"id": "template-1", "name": "Morning"}]},
        {"status": "ok", "message": "started"},
    ]
    sent, fake_out, _console = command_harness(monkeypatch, templates, responses)

    templates.cmd_templates(SimpleNamespace(action="start", template="morning"))

    assert sent == [
        {"action": "get_templates"},
        {"action": "start_template", "id": "template-1"},
    ]
    assert fake_out.data[-1][0]["message"] == "started"


def test_human_status_renders_active_pomodoro_pending_unlock_and_schedule(monkeypatch):
    response = {
        "status": "ok",
        "active": True,
        "mode": "blacklist",
        "session_type": "pomodoro",
        "remaining_seconds": 600,
        "duration_minutes": 30,
        "domains_count": 4,
        "expires_at": "2030-01-01T00:00:00",
        "pomo_phase": "focus",
        "pomo_current_cycle": 2,
        "pomo_total_cycles": 4,
        "pomo_phase_remaining": 300,
        "pomo_phase_total": 1500,
        "pending_unlock": "2030-01-01T00:10:00",
        "pending_unlock_seconds": 120,
        "schedules": [{"mode": "whitelist", "session_type": "standard", "starts_at": "10:00", "starting_in_seconds": 90}],
    }
    sent, fake_out, fake_console = command_harness(monkeypatch, status, [response], human=True)

    status.cmd_status(SimpleNamespace())

    assert sent == [{"action": "status"}]
    assert fake_out.data == []
    assert len(fake_console.renderables) == 2


@pytest.mark.parametrize("action", ["start", "stop"])
def test_web_command_reports_daemon_owned_dashboard(monkeypatch, action):
    fake_out = FakeOut()
    monkeypatch.setattr(web, "out", fake_out)

    web.cmd_web(SimpleNamespace(action=action))

    assert fake_out.data[-1][0]["status"] == "ok"
    assert "daemon" in fake_out.data[-1][0]["message"]
