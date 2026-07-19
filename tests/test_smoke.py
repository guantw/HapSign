"""Smoke tests: 验证核心模块可以正常导入和实例化。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_imports():
    """验证所有核心模块可导入。"""
    import hapsign
    import hapsign.config
    import hapsign.models
    import hapsign.pipeline
    import hapsign.api.client
    import hapsign.api.cert_api
    import hapsign.api.device_api
    import hapsign.api.provision_api
    import hapsign.api.capability_api
    import hapsign.signing.hap_signer
    import hapsign.signing.installer
    import hapsign.signing.keytool_util
    assert hapsign.__version__


def test_models():
    """验证数据模型可正常构造。"""
    from hapsign.models import TokenInfo, CertResult, AppBriefInfo, ProvisionResult
    t = TokenInfo(access_token="abc", user_id="123")
    assert t.access_token == "abc"
    c = CertResult(cert_object_id="oid", cert_id="cid")
    assert c.cert_id == "cid"
    a = AppBriefInfo(app_id="aid", project_id="pid", pure_flag=1)
    assert a.pure_flag == 1
    p = ProvisionResult(provision_file_url="url", provision_id="pid2")
    assert p.provision_file_url == "url"


def test_config_constants():
    """验证关键配置常量存在。"""
    from hapsign.config import (
        CLOUD_BASE_URL, HAP_SIGN_TOOL, HDC_PATH,
        API_TEST_PROVISION_ADD, API_REAL_PROVISION_ADD,
        ACL_PERMISSION_WHITELIST,
    )
    assert "connect-api" in CLOUD_BASE_URL
    assert "test/provision/add" in API_TEST_PROVISION_ADD
    assert "provision/add" in API_REAL_PROVISION_ADD
    assert len(ACL_PERMISSION_WHITELIST) > 0


if __name__ == "__main__":
    test_imports()
    test_models()
    test_config_constants()
    print("All smoke tests passed.")
