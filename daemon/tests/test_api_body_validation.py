from __future__ import annotations

from io import BytesIO

import pytest

from forcefocus.api_http import EmbeddedWebHandler, RequestBodyError


def _handler(body: bytes, content_length: str | None = None):
    handler = EmbeddedWebHandler.__new__(EmbeddedWebHandler)
    handler.headers = {
        "Content-Length": content_length if content_length is not None else str(len(body))
    }
    handler.rfile = BytesIO(body)
    return handler


def test_invalid_json_body_is_rejected():
    with pytest.raises(RequestBodyError) as exc:
        _handler(b"{bad json")._read_body()

    assert exc.value.status == 400


def test_non_object_json_body_is_rejected():
    with pytest.raises(RequestBodyError) as exc:
        _handler(b"[]")._read_body()

    assert exc.value.status == 400


def test_oversized_body_is_rejected():
    with pytest.raises(RequestBodyError) as exc:
        _handler(b"", str(10 * 1024 * 1024 + 1))._read_body()

    assert exc.value.status == 413
