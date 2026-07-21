"""Profile（调试配置文件）API —— 创建和查询调试 Provision Profile。

对应 AutoSigningConfigsService 中的 Profile 操作：
- add_test_provision: 创建测试 Profile（POST JSON, add.test.provision）
- add_real_provision: 创建 Real Profile（POST JSON, add.real.provision）
- delete_provision: 删除 Profile（DELETE）
- get_provision_list: 查询已创建的 Profile 列表

Test Profile（APL=normal）和 Real Profile（APL=system_basic）的区别：
- Test: 请求体只有 certList/packageName/deviceList/provisionName/aclPermissionList
- Real: 额外需要 appId/provisionType="1"/reqSource="IDE"
- Real 的响应中 provisionFileUrl 可能嵌套在 provisionInfo 下
"""

import json
import logging
from typing import Any

from hapsign.api.client import HuaweiSignClient
from hapsign.config import (
    API_PROVISION_DELETE,
    API_PROVISION_LIST,
    API_REAL_PROVISION_ADD,
    API_TEST_PROVISION_ADD,
)
from hapsign.models import ProvisionResult

logger = logging.getLogger(__name__)


class ProvisionAPI:
    """调试 Profile 相关 API 封装。"""

    def __init__(self, client: HuaweiSignClient):
        """初始化。

        Args:
            client: 已认证的 HuaweiSignClient 实例。
        """
        self._client = client

    def add_test_provision(
        self,
        team_id: str,
        bundle_name: str,
        cert_list: list[str],
        device_list: list[str],
        acl_permissions: list[str] | None = None,
    ) -> ProvisionResult:
        """创建测试 Profile（APL=normal）。

        POST JSON {API_TEST_PROVISION_ADD}
        请求体：{certList, packageName, deviceList, provisionName, aclPermissionList}

        Args:
            team_id: 团队 ID。
            bundle_name: 应用包名（packageName）。
            cert_list: 证书 id 列表。
            device_list: 设备 id 列表。
            acl_permissions: ACL 权限列表。

        Returns:
            ProvisionResult 包含下载 URL 和 provision ID。
        """
        provision_name = bundle_name

        body: dict[str, Any] = {
            "certList": cert_list,
            "packageName": bundle_name,
            "deviceList": device_list,
            "provisionName": provision_name,
            "aclPermissionList": acl_permissions or [],
        }

        return self._add_provision(API_TEST_PROVISION_ADD, team_id, body, is_real=False)

    def add_real_provision(
        self,
        team_id: str,
        bundle_name: str,
        cert_list: list[str],
        device_list: list[str],
        app_id: str,
        acl_permissions: list[str] | None = None,
    ) -> ProvisionResult:
        """创建 Real Profile（APL=system_basic）。

        POST JSON {API_REAL_PROVISION_ADD}
        请求体在 Test 基础上额外添加：appId, provisionType="1", reqSource="IDE"

        Real Profile 的 provisionFileUrl 可能嵌套在 provisionInfo 字段下
        （逆向自 AutoSigningConfigsService.parsingProvisionUrl）。

        Args:
            team_id: 团队 ID。
            bundle_name: 应用包名（packageName）。
            cert_list: 证书 id 列表。
            device_list: 设备 id 列表。
            app_id: 应用 ID（从 app.info 接口获取）。
            acl_permissions: ACL 权限列表。

        Returns:
            ProvisionResult 包含下载 URL 和 provision ID。
        """
        provision_name = f"auto_{bundle_name}"

        body: dict[str, Any] = {
            "certList": cert_list,
            "packageName": bundle_name,
            "deviceList": device_list,
            "provisionName": provision_name,
            "appId": app_id,
            "provisionType": "1",
            "reqSource": "IDE",
            "aclPermissionList": acl_permissions or [],
        }

        return self._add_provision(API_REAL_PROVISION_ADD, team_id, body, is_real=True)

    def _add_provision(
        self,
        url: str,
        team_id: str,
        body: dict[str, Any],
        is_real: bool,
    ) -> ProvisionResult:
        """执行 provision/add 请求并解析响应。

        Java 用 responseContent.contains('"code":0') 检查成功，
        所以用原始文本的子串匹配。
        """
        headers = self._client._get_headers(team_id)
        text = self._client._do_post_json_text(url, headers, body)
        logger.debug("provision/add response: %s", text[:500])

        if '"code":0' not in text:
            raise RuntimeError(f"add_provision failed: {text}")

        # 解析响应获取 provisionFileUrl 和 id
        data = json.loads(text)

        # Real Provision 的 provisionFileUrl 可能嵌套在 provisionInfo 下
        target = data
        if is_real:
            provision_info = data.get("provisionInfo")
            if provision_info:
                target = provision_info

        download_url = str(target.get("provisionFileUrl", ""))
        provision_id = str(target.get("id", ""))
        provision_name = str(body.get("provisionName", ""))

        if not download_url:
            raise ValueError(f"provision/add 响应中未包含 provisionFileUrl: {data}")

        return ProvisionResult(
            provision_file_url=download_url,
            provision_id=provision_id,
            provision_name=provision_name,
        )

    def delete_provision(self, team_id: str, provision_id: str) -> bool:
        """删除指定 Profile。

        DELETE {API_PROVISION_DELETE}?id={provision_id}
        body JSON 空。逆向自 AutoSigningConfigsService.deleteProvision。

        Args:
            team_id: 团队 ID。
            provision_id: Profile ID。

        Returns:
            删除成功返回 True。
        """
        if not provision_id:
            return True

        url = f"{API_PROVISION_DELETE}?id={provision_id}"
        headers = self._client._get_headers(team_id)
        text = self._client._do_delete_text(url, headers, {})
        return '"code":0' in text

    def get_provision_list(
        self,
        team_id: str,
        app_id: str = "",
    ) -> list[dict[str, Any]]:
        """查询已创建的 Profile 列表。

        GET {API_PROVISION_LIST}
        参数：encodeFlag=0, start=1, pageSize=100

        Args:
            team_id: 团队 ID。
            app_id: 应用 ID（在 header 中传递）。

        Returns:
            Profile 列表，每个元素包含证书、设备、权限等信息。
        """
        headers = self._client._get_headers(team_id)
        if app_id:
            headers["appId"] = app_id
        params = {"encodeFlag": "0", "start": "1", "pageSize": "100"}
        data = self._client._do_get(API_PROVISION_LIST, headers, params)
        return data.get("list") or []
