"""轻量 API 封装的请求和响应测试。"""

import json
from unittest.mock import Mock

import pytest
import requests

from hapsign.api.cert_api import CertAPI
from hapsign.api.client import TokenExpiredError
from hapsign.api.device_api import DeviceAPI
from hapsign.api.provision_api import ProvisionAPI
from hapsign.cancellation import OperationCancelled
from hapsign.config import (
    API_AGREEMENT,
    API_REAL_PROVISION_ADD,
    API_TEST_PROVISION_ADD,
    HEADER_ACCESS_TOKEN,
)


@pytest.fixture
def client() -> Mock:
    value = Mock()
    value._get_headers.return_value = {"teamId": "team"}
    value.access_token = "tok"
    return value


def test_add_test_provision_builds_expected_body(client) -> None:
    client._do_post_json_text.return_value = json.dumps(
        {"ret": {"code": 0}, "provisionFileUrl": "url", "id": "id"},
        separators=(",", ":"),
    )

    result = ProvisionAPI(client).add_test_provision(
        "team", "com.example", ["cert"], ["device"], ["permission"]
    )

    url, _, body = client._do_post_json_text.call_args.args
    assert url == API_TEST_PROVISION_ADD
    assert body["packageName"] == "com.example"
    assert body["aclPermissionList"] == ["permission"]
    assert result.provision_file_url == "url"


def test_add_real_provision_reads_nested_result(client) -> None:
    client._do_post_json_text.return_value = json.dumps(
        {
            "ret": {"code": 0},
            "provisionInfo": {"provisionFileUrl": "url", "id": "id"},
        },
        separators=(",", ":"),
    )

    result = ProvisionAPI(client).add_real_provision(
        "team", "com.example", ["cert"], ["device"], "app-id"
    )

    url, _, body = client._do_post_json_text.call_args.args
    assert url == API_REAL_PROVISION_ADD
    assert body["appId"] == "app-id"
    assert result.provision_id == "id"


def test_provision_rejects_error_response(client) -> None:
    client._do_post_json_text.return_value = '{"ret":{"code":1}}'

    with pytest.raises(RuntimeError, match="add_provision failed"):
        ProvisionAPI(client).add_test_provision("team", "bundle", [], [])


@pytest.mark.parametrize("duplicate_code", [205389857, 205389858, "205389858"])
def test_add_device_treats_duplicate_code_as_success(client, duplicate_code) -> None:
    client._do_post_form.return_value = {"ret": {"code": duplicate_code}}

    assert DeviceAPI(client).add_device("udid", "4", "team", "name") is True


@pytest.mark.parametrize("duplicate_code", [205389857, 205389858])
def test_add_device_treats_duplicate_in_http_error_as_success(
    client, duplicate_code
) -> None:
    client._do_post_form.side_effect = RuntimeError(f"request failed: {duplicate_code}")

    assert DeviceAPI(client).add_device("udid", "4", "team", "name") is True


def test_add_device_raises_on_other_business_error(client) -> None:
    client._do_post_form.return_value = {"ret": {"code": 1, "msg": "failed"}}

    with pytest.raises(RuntimeError, match="add_device failed"):
        DeviceAPI(client).add_device("udid", "4", "team", "name")


def test_find_device_id(client) -> None:
    client._do_get.return_value = {"list": [{"id": 7, "udid": "wanted"}]}

    assert DeviceAPI(client).find_device_id("team", "wanted") == "7"


def test_find_device_id_reports_missing_device(client) -> None:
    client._do_get.return_value = {"list": []}

    with pytest.raises(ValueError, match="Device not found"):
        DeviceAPI(client).find_device_id("team", "missing")


def test_find_certificate(client) -> None:
    client._do_get.return_value = {
        "certList": [{"certName": "debug.cer", "certObjectId": "object"}]
    }

    result = CertAPI(client).find_cert("team", "debug.cer")

    assert result["certObjectId"] == "object"


def test_sign_agreement_uses_unified_client_with_only_access_token(client) -> None:
    client._do_post_form_text.return_value = "ok"

    assert CertAPI(client).sign_agreement() is True

    url, headers = client._do_post_form_text.call_args.args
    assert url == API_AGREEMENT
    # 只保留该接口特殊的 accessToken 头，不误加签名 API 的其他认证头。
    assert headers == {HEADER_ACCESS_TOKEN: "tok"}


def test_sign_agreement_propagates_http_error(client) -> None:
    client._do_post_form_text.side_effect = requests.HTTPError("500 Server Error")

    with pytest.raises(requests.HTTPError):
        CertAPI(client).sign_agreement()


def test_sign_agreement_propagates_token_expiry(client) -> None:
    client._do_post_form_text.side_effect = TokenExpiredError("Token 失效")

    with pytest.raises(TokenExpiredError):
        CertAPI(client).sign_agreement()


def test_sign_agreement_propagates_cancellation(client) -> None:
    client._do_post_form_text.side_effect = OperationCancelled("cancelled")

    with pytest.raises(OperationCancelled):
        CertAPI(client).sign_agreement()
