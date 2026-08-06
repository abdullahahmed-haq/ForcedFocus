from __future__ import annotations

from types import SimpleNamespace

from forcefocus.api_http import EmbeddedWebHandler


EXTENSION_ORIGIN = "chrome-extension://hcgpgflhkpdccdjkkobofpaemcgjmhdc"


def test_extension_mutation_preflight_allows_authentication_headers():
    """Chrome must be allowed to preflight context-menu mutations."""
    daemon = SimpleNamespace(
        api_token="test-token",
        settings={"allowed_extension_ids": ["hcgpgflhkpdccdjkkobofpaemcgjmhdc"]},
        command_service=SimpleNamespace(dispatch=lambda command: {"status": "ok"}),
    )
    handler = object.__new__(EmbeddedWebHandler)
    handler.headers = {
        "Host": "127.0.0.1:7070",
        "Origin": EXTENSION_ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-api-token",
    }
    handler.server = SimpleNamespace(daemon_ref=daemon)
    responses: list[int] = []
    headers: dict[str, str] = {}
    handler.send_response = responses.append
    handler.send_header = headers.__setitem__
    handler.end_headers = lambda: None
    handler.send_error = lambda *_args: None

    handler.do_OPTIONS()

    assert responses == [204]
    assert headers["Access-Control-Allow-Origin"] == EXTENSION_ORIGIN
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert headers["Access-Control-Allow-Headers"] == "Content-Type, X-API-Token"
