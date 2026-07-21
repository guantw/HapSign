"""应用信息与 Capability API —— 查询应用简要信息。

对应 AutoSigningConfigsService.getAppBriefInfoList 和
CapabilityUtil.subscribe 的部分功能。

Real Provision 路径需要 appId 和 projectId，通过 app.info 接口查询。
subscribe 步骤在无 capabilities 时为 no-op，命令行工具中跳过。
"""

import logging

from hapsign.api.client import HuaweiSignClient
from hapsign.config import API_APP_BRIEF_INFO
from hapsign.models import AppBriefInfo

logger = logging.getLogger(__name__)


class CapabilityAPI:
    """应用信息与 Capability 相关 API 封装。"""

    def __init__(self, client: HuaweiSignClient):
        """初始化。

        Args:
            client: 已认证的 HuaweiSignClient 实例。
        """
        self._client = client

    def get_app_brief_info(self, team_id: str, bundle_name: str) -> AppBriefInfo | None:
        """查询应用简要信息，获取 appId 和 projectId。

        GET {API_APP_BRIEF_INFO}
        参数：packageNames={bundleName}
        从响应的 appInfos 列表提取 appId、projectId、pureFlag。

        逆向自 AutoSigningConfigsService.getAppBriefInfoList。
        Real Provision 需要 appId 放入请求体。

        Args:
            team_id: 团队 ID。
            bundle_name: 应用包名。

        Returns:
            AppBriefInfo 实例，如果未找到返回 None。
        """
        headers = self._client._get_headers(team_id)
        params = {"packageNames": bundle_name}
        data = self._client._do_get(API_APP_BRIEF_INFO, headers, params)

        app_infos = data.get("appInfos") or []
        if not app_infos:
            logger.warning("app.info 响应中 appInfos 为空")
            return None

        info = app_infos[0]
        return AppBriefInfo(
            app_id=str(info.get("appId", "")),
            project_id=str(info.get("projectId", "")),
            pure_flag=int(info.get("pureFlag", 0)),
            bundle_name=bundle_name,
        )
