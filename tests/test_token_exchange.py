"""Token 交换和 JWT 解析测试。"""

import base64
import json
from unittest.mock import Mock

import pytest

from hapsign.token import token_exchange


def _jwt(payload: dict) -> str:
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    return f"header.{encoded}.signature"


def test_exchange_temp_token(monkeypatch) -> None:
    response = Mock(text="jwt-token")
    monkeypatch.setattr(token_exchange.requests, "get", Mock(return_value=response))

    result = token_exchange.TokenExchange().exchange_temp_token("temp", "CN", "1.0")

    assert result == "jwt-token"
    response.raise_for_status.assert_called_once()


def test_get_access_token_decodes_user(monkeypatch) -> None:
    jwt = _jwt({"userName": "tester", "userId": "42"})
    response = Mock()
    response.json.return_value = {
        "status": True,
        "userInfo": {
            "accessToken": "access",
            "refreshToken": "refresh",
            "nationalCode": "CN",
            "realName": True,
        },
    }
    monkeypatch.setattr(token_exchange.requests, "get", Mock(return_value=response))

    result = token_exchange.TokenExchange().get_access_token(jwt)

    assert result.access_token == "access"
    assert result.user_id == "42"
    assert result.user_name == "tester"
    assert result.real_name is True


def test_get_access_token_rejects_unsuccessful_response(monkeypatch) -> None:
    response = Mock()
    response.json.return_value = {"status": False}
    monkeypatch.setattr(token_exchange.requests, "get", Mock(return_value=response))

    with pytest.raises(ValueError, match="status=false"):
        token_exchange.TokenExchange().get_access_token("invalid")


def test_refresh_access_token(monkeypatch) -> None:
    response = Mock()
    response.json.return_value = {"status": True, "userInfo": {"accessToken": "new"}}
    get = Mock(return_value=response)
    monkeypatch.setattr(token_exchange.requests, "get", get)

    assert token_exchange.TokenExchange().refresh_access_token("jwt") == "new"
    assert get.call_args.kwargs["headers"]["refresh"] == "true"
