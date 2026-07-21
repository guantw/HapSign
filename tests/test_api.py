"""轻量 API 封装的请求和响应测试。"""

import json
from unittest.mock import Mock

import pytest

from hapsign.api.cert_api import CertAPI
from hapsign.api.device_api import DeviceAPI
from hapsign.api.provision_api import ProvisionAPI
from hapsign.config import API_REAL_PROVISION_ADD, API_TEST_PROVISION_ADD


@pytest.fixture
def client() -> Mock:
    value = Mock()
    value._get_headers.return_value = {"teamId": "team"}
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
