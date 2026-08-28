"""HTTP 客户端响应处理测试。"""

import logging
from unittest.mock import Mock

import pytest

from hapsign.api import client as client_module
from hapsign.api.client import HuaweiSignClient, TokenExpiredError
from hapsign.config import (
    ACCEPT_LANGUAGE,
    HEADER_ACCEPT_LANGUAGE,
    HEADER_OAUTH2_TOKEN,
    HEADER_TEAM_ID,
    HEADER_UID,
    HEADER_USER_AGENT,
    USER_AGENT,
)
from hapsign.diagnostics import set_sensitive_logging


def test_headers_include_credentials_and_optional_team() -> None:
    client = HuaweiSignClient("access-token", "user-id")

    headers = client._get_headers("team-id")

    assert headers[HEADER_OAUTH2_TOKEN] == "access-token"
    assert headers[HEADER_UID] == "user-id"
    assert headers[HEADER_TEAM_ID] == "team-id"
    assert headers[HEADER_USER_AGENT] == USER_AGENT
    assert headers[HEADER_ACCEPT_LANGUAGE] == ACCEPT_LANGUAGE
    assert HEADER_USER_AGENT == "User-Agent"
    assert HEADER_ACCEPT_LANGUAGE == "Accept-Language"
    assert "Chrome/" in headers[HEADER_USER_AGENT]
    assert USER_AGENT not in headers  # 值不能被误当成 header 名


@pytest.mark.parametrize(
    "payload",
    [
        {"code": 4000, "message": "expired"},
        {"ret": {"code": 4000, "msg": "expired"}},
    ],
)
def test_check_response_detects_token_expiry_in_json(payload) -> None:
    response = Mock(status_code=200)
    response.json.return_value = payload
    client = HuaweiSignClient("access-token", "user-id")

    with pytest.raises(TokenExpiredError):
        client._check_response(response)


def test_check_response_detects_http_401() -> None:
    response = Mock(status_code=401, reason="Unauthorized")
    client = HuaweiSignClient("access-token", "user-id")

    with pytest.raises(TokenExpiredError):
        client._check_response(response)


def test_build_url_routes_absolute_and_relative_paths() -> None:
    client = HuaweiSignClient("access-token", "user-id", "https://api.example")

    assert client._build_url("https://download.example/file") == (
        "https://download.example/file"
    )
    assert client._build_url("/api/example") == "https://api.example/api/example"
    assert "devecostudio.huawei.com" in client._build_url("/authrouter/example")


@pytest.mark.parametrize(
    ("method_name", "request_name", "expected"),
    [
        ("_do_get", "get", {"method": "get"}),
        ("_do_post_form", "post", {"method": "post"}),
        ("_do_post_json", "post", {"method": "post"}),
        ("_do_delete", "delete", {"method": "delete"}),
        ("_do_post_form_text", "post", "post text"),
        ("_do_delete_text", "delete", "delete text"),
        ("_do_post_json_text", "post", "post text"),
    ],
)
def test_request_helpers(monkeypatch, method_name, request_name, expected) -> None:
    response = Mock(status_code=200)
    response.json.return_value = (
        expected if isinstance(expected, dict) else {"status": "ok"}
    )
    response.text = expected if isinstance(expected, str) else "json"
    request = Mock(return_value=response)
    monkeypatch.setattr(client_module.requests, request_name, request)
    client = HuaweiSignClient("access-token", "user-id", "https://api.example")

    method = getattr(client, method_name)
    result = method("/path", {"header": "value"}, {"key": "value"})

    assert result == expected
    response.raise_for_status.assert_called_once()


def test_http_diagnostics_require_sensitive_switch(caplog) -> None:
    response = Mock(
        status_code=200,
        content=b'{"secret":"response-secret"}',
        text='{"secret":"response-secret"}',
    )
    response.json.return_value = {"ret": {"code": 0}, "secret": "response-secret"}
    client = HuaweiSignClient("access-token", "user-id")
    caplog.set_level(logging.DEBUG)

    try:
        set_sensitive_logging(False)
        client._log_http(
            "POST",
            "https://example.invalid/path?secret=query-secret",
            response,
            headers={"oauth2Token": "header-secret"},
            payload={"token": "payload-secret"},
        )
        assert "header-secret" not in caplog.text
        assert "payload-secret" not in caplog.text
        assert "response-secret" not in caplog.text
        assert "query-secret" not in caplog.text

        caplog.clear()
        set_sensitive_logging(True)
        client._log_http(
            "POST",
            "https://example.invalid/path?secret=query-secret",
            response,
            headers={"oauth2Token": "header-secret"},
            payload={"token": "payload-secret"},
        )
        assert "header-secret" in caplog.text
        assert "payload-secret" in caplog.text
        assert "response-secret" in caplog.text
        assert "query-secret" in caplog.text
    finally:
        set_sensitive_logging(False)
