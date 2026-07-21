"""基础导入和数据模型测试。"""

import hapsign
import hapsign.api.capability_api
import hapsign.api.cert_api
import hapsign.api.client
import hapsign.api.device_api
import hapsign.api.provision_api
import hapsign.config
import hapsign.models
import hapsign.pipeline
import hapsign.signing.hap_signer
import hapsign.signing.installer
import hapsign.signing.keytool_util
from hapsign.models import AppBriefInfo, CertResult, ProvisionResult, TokenInfo


def test_version_is_defined() -> None:
    assert hapsign.__version__


def test_models_can_be_constructed() -> None:
    token = TokenInfo(access_token="abc", user_id="123")
    cert = CertResult(cert_object_id="oid", cert_id="cid")
    app = AppBriefInfo(app_id="aid", project_id="pid", pure_flag=1)
    provision = ProvisionResult(provision_file_url="url", provision_id="pid2")

    assert token.access_token == "abc"
    assert cert.cert_id == "cid"
    assert app.pure_flag == 1
    assert provision.provision_file_url == "url"


def test_config_constants() -> None:
    assert "connect-api" in hapsign.config.CLOUD_BASE_URL
    assert "test/provision/add" in hapsign.config.API_TEST_PROVISION_ADD
    assert "provision/add" in hapsign.config.API_REAL_PROVISION_ADD
    assert hapsign.config.ACL_PERMISSION_WHITELIST
