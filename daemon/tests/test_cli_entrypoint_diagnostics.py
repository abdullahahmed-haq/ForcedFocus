"""Public CLI parser, output, doctor, and diagnostic-bundle contracts."""

from __future__ import annotations

import json
import sys
import zipfile
from types import SimpleNamespace

import pytest

from cli import main as cli_main
from cli.commands import diagnostics, doctor
from cli.output import OutputHandler

from .test_cli_contracts import CLIError, FakeConsole, FakeOut


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["start", "--duration", "45", "--mode", "whitelist", "--groups", "work", "docs"], {"command": "start", "duration": 45, "mode": "whitelist", "groups": ["work", "docs"]}),
        (["stop", "--key", "secret"], {"command": "stop", "key": "secret"}),
        (["schedule", "add", "--recurring", "--days", "0,2", "--time", "09:00"], {"command": "schedule", "action": "add", "recurring": True, "days": "0,2"}),
        (["schedule", "edit", "rule-1", "--enabled", "false", "--duration", "90"], {"command": "schedule", "action": "edit", "id": "rule-1", "enabled": "false"}),
        (["perma-block", "unblock", "social.example", "--key", "secret"], {"command": "perma-block", "action": "unblock", "domain": "social.example"}),
        (["domains", "add", "blacklist", "one.example", "two.example"], {"command": "domains", "action": "add", "domains": ["one.example", "two.example"]}),
        (["settings", "set", "intent_notification_enabled", "true"], {"command": "settings", "action": "set", "key": "intent_notification_enabled"}),
        (["templates", "add", "Morning", "--type", "pomodoro", "--cycles", "3"], {"command": "templates", "action": "add", "name": "Morning", "session_type": "pomodoro"}),
        (["diagnostics", "--output", "/tmp/report.zip"], {"command": "diagnostics", "output": "/tmp/report.zip"}),
        (["doctor", "--json"], {"command": "doctor", "json": True}),
    ],
)
def test_parser_preserves_documented_command_shapes(argv, expected):
    args = cli_main.build_parser().parse_args(argv)

    for key, value in expected.items():
        assert getattr(args, key) == value
    assert callable(args.func)


def test_rich_help_lists_release_commands(monkeypatch):
    fake_console = FakeConsole()
    monkeypatch.setattr(cli_main, "console", fake_console)

    cli_main.print_rich_help(cli_main.build_parser())

    assert len(fake_console.renderables) == 6
    commands_panel = fake_console.renderables[3][0]
    assert "Available Commands" in commands_panel.title
    assert commands_panel.renderable.row_count == 14


def test_main_brief_in_noninteractive_mode_is_machine_readable(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["forcefocus", "--brief", "--agent"])

    cli_main.main()

    payload = json.loads(capsys.readouterr().out)
    assert "high-integrity productivity system" in payload["brief"]


def test_main_dispatches_selected_command_and_sets_agent_output(monkeypatch):
    fake_out = FakeOut(human=True)
    called = []
    parser = SimpleNamespace(
        parse_known_args=lambda: (SimpleNamespace(brief=False, command="status", agent=True, human=False), []),
        parse_args=lambda: SimpleNamespace(func=lambda args: called.append(args), command="status"),
    )
    monkeypatch.setattr(sys, "argv", ["forcefocus", "--agent", "status"])
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(cli_main, "out", fake_out)

    cli_main.main()

    assert len(called) == 1
    assert called[0].command == "status"
    assert fake_out.is_agent is True
    assert fake_out.is_human is False


def test_main_converts_unhandled_command_exception_to_structured_error(monkeypatch):
    fake_out = FakeOut()

    def fail(_args):
        raise RuntimeError("unexpected failure")

    parser = SimpleNamespace(
        parse_known_args=lambda: (SimpleNamespace(brief=False, command="status", agent=True, human=False), []),
        parse_args=lambda: SimpleNamespace(func=fail, command="status"),
    )
    monkeypatch.setattr(sys, "argv", ["forcefocus", "--agent", "status"])
    monkeypatch.setattr(cli_main, "build_parser", lambda: parser)
    monkeypatch.setattr(cli_main, "out", fake_out)

    with pytest.raises(CLIError) as exc_info:
        cli_main.main()

    assert exc_info.value.code == "INTERNAL_ERROR"
    assert str(exc_info.value) == "unexpected failure"


def test_output_handler_agent_mode_emits_json_data(capsys):
    handler = OutputHandler(use_agent=True)

    handler.print_data({"status": "ok", "message": "ready"})

    assert json.loads(capsys.readouterr().out) == {"status": "ok", "message": "ready"}


def test_output_handler_agent_errors_have_stable_exit_code_and_shape(capsys):
    handler = OutputHandler(use_agent=True)

    with pytest.raises(SystemExit) as exc_info:
        handler.print_error("bad option", code="USAGE_ERROR", suggestion="See --help")

    assert exc_info.value.code == 2
    assert json.loads(capsys.readouterr().err) == {
        "error": True,
        "code": "USAGE_ERROR",
        "message": "bad option",
        "suggestion": "See --help",
    }


@pytest.mark.parametrize("status", ["ok", "pending", "error", "custom"])
def test_output_handler_human_mode_renders_every_response_state(monkeypatch, status):
    fake_console = FakeConsole()
    monkeypatch.setattr("cli.output.console", fake_console)
    handler = OutputHandler(use_human=True)

    handler.print_data({"status": status, "message": "result"}, title="Test")

    panel = fake_console.renderables[-1][0]
    assert panel.title == "Test"
    assert "result" in str(panel.renderable)


def test_doctor_summary_combines_daemon_schema_platform_enforcement_and_disk(monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / "state_manifest.json").write_text(
        json.dumps({"schema_version": doctor.STATE_SCHEMA_VERSION}), encoding="utf-8"
    )
    hosts = tmp_path / "hosts"
    hosts.write_text("# BEGIN FORCEFOCUS ─\n# BEGIN FORCEFOCUS PERMANENT", encoding="utf-8")
    plist = tmp_path / "daemon.plist"
    plist.write_text("plist", encoding="utf-8")
    monkeypatch.setattr(doctor, "CONFIG_DIR", config)
    monkeypatch.setattr(doctor, "HOSTS_PATH", hosts)
    monkeypatch.setattr(doctor, "PLIST_PATH", plist)
    monkeypatch.setattr(doctor, "_socket_health", lambda: {"status": "ok", "recovery_required": False})
    monkeypatch.setattr(doctor, "_http_health", lambda: {"status": "ok"})
    monkeypatch.setattr(doctor.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(doctor.platform, "mac_ver", lambda: ("15.0", (), ""))
    monkeypatch.setattr(doctor.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        doctor.os,
        "statvfs",
        lambda _path: SimpleNamespace(f_bavail=1024 * 1024, f_frsize=1024),
    )

    checks, summary = doctor.gather_checks()

    by_name = {check.name: check for check in checks}
    assert summary["status"] == "ok"
    assert by_name["platform"].status == "ok"
    assert by_name["state_schema"].status == "ok"
    assert by_name["unix_socket"].detail == "connected"
    assert by_name["hosts_markers"].detail == "session=1 permanent=1"
    assert by_name["disk_space"].status == "ok"


def test_doctor_socket_and_http_health_failures_are_structured(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "SOCK_PATH", tmp_path / "missing.sock")
    assert doctor._socket_health() == {"status": "error", "error_code": "DAEMON_NOT_FOUND"}

    monkeypatch.setattr(
        doctor.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    response = doctor._http_health()
    assert response["error_code"] == "HTTP_FAILURE"
    assert response["message"] == "offline"


def test_diagnostic_bundle_contains_metadata_and_only_redacted_log_tail(monkeypatch, tmp_path):
    log = tmp_path / "forcefocus.log"
    log.write_text(
        "user /Users/alice visited news.example\n"
        "api_token=0123456789abcdef0123456789abcdef\n"
        "coordinates 30.044400 and 31.235700\n",
        encoding="utf-8",
    )
    destination = tmp_path / "diagnostics.zip"
    fake_out = FakeOut()
    monkeypatch.setattr(diagnostics, "LOG_PATHS", (log, tmp_path / "missing.log"))
    monkeypatch.setattr(
        diagnostics,
        "gather_checks",
        lambda: ([], {"status": "ok", "checks": []}),
    )
    monkeypatch.setattr(diagnostics, "out", fake_out)

    diagnostics.cmd_diagnostics(SimpleNamespace(output=str(destination)))

    with zipfile.ZipFile(destination) as archive:
        assert set(archive.namelist()) == {"diagnostics.json", "logs/forcefocus.log"}
        metadata = json.loads(archive.read("diagnostics.json"))
        redacted = archive.read("logs/forcefocus.log").decode()
    assert metadata["doctor"]["status"] == "ok"
    assert "/Users/alice" not in redacted
    assert "news.example" not in redacted
    assert "30.044400" not in redacted
    assert "[REDACTED_SENSITIVE_LOG_LINE]" in redacted
    assert fake_out.data[-1][0]["output"] == str(destination)
