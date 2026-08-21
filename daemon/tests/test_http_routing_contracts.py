"""Transport-level contracts for the local HTTP API.

The handler is driven through its HTTP verb methods while response I/O is kept
in memory.  This exercises the real routing/authentication code without
opening a port or touching machine state.
"""

from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from forcefocus.api_http import EmbeddedWebHandler


class RecordingCommandService:
    def __init__(self):
        self.commands: list[dict] = []

    def dispatch(self, command: dict) -> dict:
        self.commands.append(command)
        return {"status": "ok", "echo": command}


def make_handler(
    path: str,
    *,
    body: object | None = None,
    host: str = "127.0.0.1:7070",
    origin: str | None = "http://127.0.0.1:7070",
    token: str | None = "secret-token",
    web_dir=None,
):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    headers = {"Host": host, "Content-Length": str(len(encoded))}
    if origin is not None:
        headers["Origin"] = origin
    if token is not None:
        headers["X-API-Token"] = token

    service = RecordingCommandService()
    daemon = SimpleNamespace(
        api_token="secret-token",
        command_service=service,
        settings={"allowed_extension_ids": ["trusted-extension"]},
    )
    handler = EmbeddedWebHandler.__new__(EmbeddedWebHandler)
    handler.path = path
    handler.headers = headers
    handler.rfile = BytesIO(encoded)
    handler.wfile = BytesIO()
    handler.server = SimpleNamespace(
        daemon_ref=daemon,
        web_dir=web_dir,
    )
    handler.response_status = None
    handler.response_headers = []
    handler.send_response = lambda status: setattr(handler, "response_status", status)
    handler.send_header = lambda key, value: handler.response_headers.append((key, value))
    handler.end_headers = lambda: None
    handler.send_error = MagicMock()
    return handler, service


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/health", {"action": "health"}),
        ("/api/status", {"action": "status"}),
        ("/api/sounds", {"action": "get_sounds"}),
        ("/api/prayer", {"action": "get_prayer"}),
        ("/api/lists", {"action": "get_lists"}),
        ("/api/groups", {"action": "get_groups"}),
        ("/api/settings", {"action": "get_settings"}),
        ("/api/templates", {"action": "get_templates"}),
        ("/api/schedules/recurring", {"action": "get_recurring_schedules"}),
        ("/api/perma-blocklist", {"action": "get_perma_blocklist"}),
        ("/api/session-domains", {"action": "get_session_domains"}),
        ("/api/sleep-schedule", {"action": "get_sleep_schedule"}),
        ("/api/history?limit=10&kind=pomodoro", {"action": "get_history", "query": {"limit": "10", "kind": "pomodoro"}}),
    ],
)
def test_get_routes_dispatch_the_documented_command(path, expected):
    handler, service = make_handler(path)

    handler.do_GET()

    assert handler.response_status == 200
    assert service.commands == [expected]
    assert json.loads(handler.wfile.getvalue())["echo"] == expected


@pytest.mark.parametrize(
    ("path", "body", "expected"),
    [
        (
            "/api/start",
            {"duration": 45, "mode": "whitelist", "groups": ["work"], "schedule_in": 10},
            {
                "action": "start",
                "duration_minutes": 45,
                "mode": "whitelist",
                "session_type": "standard",
                "focus_minutes": 25,
                "break_minutes": 5,
                "cycles": 4,
                "groups": ["work"],
                "intent": "",
                "intent_tasks": [],
                "schedule_in_minutes": 10,
            },
        ),
        ("/api/stop", {"key": "passphrase"}, {"action": "stop", "key": "passphrase"}),
        ("/api/cancel-stop", {"ignored": True}, {"action": "cancel_stop"}),
        ("/api/intent", {"intent": "Write", "tasks": []}, {"action": "set_intent", "intent": "Write", "tasks": []}),
        ("/api/settings", {"settings": {"sound_start": "bell.wav"}}, {"action": "save_settings", "settings": {"sound_start": "bell.wav"}}),
        ("/api/prayer/skip", {"prayer": "Fajr"}, {"action": "skip_prayer", "prayer": "Fajr"}),
        ("/api/sleep-schedule", {"enabled": False}, {"action": "save_sleep_schedule", "enabled": False}),
        ("/api/schedules/recurring", {"name": "Deep work"}, {"action": "add_recurring_schedule", "name": "Deep work"}),
        ("/api/schedules/recurring/rule-1", {"name": "Renamed"}, {"action": "update_recurring_schedule", "id": "rule-1", "name": "Renamed"}),
        ("/api/schedules/recurring/rule-1/pause", {}, {"action": "pause_recurring_schedule", "id": "rule-1"}),
        ("/api/schedules/recurring/rule-1/resume", {}, {"action": "resume_recurring_schedule", "id": "rule-1"}),
        ("/api/schedules/recurring/rule-1/duplicate", {"name": "Copy"}, {"action": "duplicate_recurring_schedule", "id": "rule-1", "name": "Copy"}),
        ("/api/templates", {"name": "Morning"}, {"action": "add_template", "name": "Morning"}),
        ("/api/templates/template-1", {"name": "Evening"}, {"action": "update_template", "id": "template-1", "name": "Evening"}),
        ("/api/templates/template-1/start", {"ignored": True}, {"action": "start_template", "id": "template-1"}),
        ("/api/templates/template-1/duplicate", {"name": "Copy"}, {"action": "duplicate_template", "id": "template-1", "name": "Copy"}),
        ("/api/lists/blacklist", {"domain": "example.com"}, {"action": "add_domain", "list": "blacklist", "domain": "example.com"}),
        ("/api/lists/whitelist/bulk", {"domains": ["docs.example"]}, {"action": "add_domains", "list": "whitelist", "domains": ["docs.example"]}),
        ("/api/groups", {"name": "work", "domains": ["docs.example"]}, {"action": "add_group", "name": "work", "domains": ["docs.example"]}),
        ("/api/perma-blocklist", {"domains": ["social.example"]}, {"action": "add_perma_block", "domain": "", "domains": ["social.example"]}),
        ("/api/perma-blocklist/unblock", {"domain": "social.example", "key": "secret"}, {"action": "request_perma_unblock", "domain": "social.example", "key": "secret"}),
        ("/api/perma-blocklist/cancel-unblock", {"domain": "social.example"}, {"action": "cancel_perma_unblock", "domain": "social.example"}),
    ],
)
def test_post_routes_translate_http_documents_to_daemon_commands(path, body, expected):
    handler, service = make_handler(path, body=body)

    handler.do_POST()

    assert handler.response_status == 200
    assert service.commands == [expected]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/api/lists/blacklist/news.example/path", {"action": "remove_domain", "list": "blacklist", "domain": "news.example/path"}),
        ("/api/groups/work", {"action": "remove_group", "name": "work"}),
        ("/api/schedules/recurring/rule-1", {"action": "remove_recurring_schedule", "id": "rule-1"}),
        ("/api/templates/template-1", {"action": "remove_template", "id": "template-1"}),
        ("/api/history", {"action": "clear_history"}),
    ],
)
def test_delete_routes_dispatch_the_expected_command(path, expected):
    handler, service = make_handler(path)

    handler.do_DELETE()

    assert handler.response_status == 200
    assert service.commands == [expected]


def test_protected_get_requires_a_valid_token():
    handler, service = make_handler("/api/settings", token=None)

    handler.do_GET()

    assert handler.response_status == 401
    assert service.commands == []
    assert json.loads(handler.wfile.getvalue())["error_code"] == "UNAUTHORIZED"


@pytest.mark.parametrize("verb", ["do_GET", "do_POST", "do_DELETE"])
def test_api_rejects_untrusted_hosts_before_dispatch(verb):
    handler, service = make_handler("/api/status", body={}, host="attacker.example")

    getattr(handler, verb)()

    handler.send_error.assert_called_once_with(403, "Forbidden: invalid Host header")
    assert service.commands == []


def test_post_rejects_cross_origin_requests_before_reading_body():
    handler, service = make_handler("/api/start", body={"duration": 30}, origin="https://attacker.example")

    handler.do_POST()

    assert handler.response_status == 403
    assert service.commands == []
    assert json.loads(handler.wfile.getvalue())["message"].startswith("CORS policy")


@pytest.mark.parametrize(
    ("raw_body", "content_length", "status", "message"),
    [
        (b"{}", "not-a-number", 400, "Invalid Content-Length header."),
        (b"{}", "-1", 400, "Invalid Content-Length header."),
        (b"[1, 2]", "6", 400, "Request body must be a JSON object."),
        (b"not-json", "8", 400, "Request body must be valid JSON."),
        (b"", str(10 * 1024 * 1024 + 1), 413, "Request body is too large."),
    ],
)
def test_post_returns_structured_errors_for_invalid_request_documents(raw_body, content_length, status, message):
    handler, service = make_handler("/api/start", body={})
    handler.rfile = BytesIO(raw_body)
    handler.headers["Content-Length"] = content_length

    handler.do_POST()

    assert handler.response_status == status
    assert service.commands == []
    assert json.loads(handler.wfile.getvalue()) == {
        "status": "error",
        "error_code": "INVALID_INPUT",
        "message": message,
    }


def test_options_exposes_only_the_local_api_cors_contract():
    handler, _service = make_handler("/api/start", origin="chrome-extension://trusted-extension")

    handler.do_OPTIONS()

    assert handler.response_status == 204
    headers = dict(handler.response_headers)
    assert headers["Access-Control-Allow-Origin"] == "chrome-extension://trusted-extension"
    assert headers["Access-Control-Allow-Methods"] == "GET, POST, DELETE, OPTIONS"
    assert "X-API-Token" in headers["Access-Control-Allow-Headers"]


def test_unknown_http_verb_returns_method_not_allowed():
    handler, _service = make_handler("/api/status")

    handler.do_PATCH()

    assert handler.response_status == 405
    assert json.loads(handler.wfile.getvalue())["error_code"] == "METHOD_NOT_ALLOWED"


def test_static_html_is_confined_to_web_root_and_receives_api_token(tmp_path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "index.html").write_text("<head></head><body>Dashboard</body>", encoding="utf-8")
    handler, _service = make_handler("/", web_dir=tmp_path)

    handler.do_GET()

    assert handler.response_status == 200
    assert b'window.apiToken = "secret-token"' in handler.wfile.getvalue()
    assert b"Dashboard" in handler.wfile.getvalue()


def test_static_file_traversal_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    handler, _service = make_handler("/../outside.txt", web_dir=tmp_path)

    handler.do_GET()

    handler.send_error.assert_called_once_with(403)
    assert handler.wfile.getvalue() == b""
