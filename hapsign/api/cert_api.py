"""证书 API —— 团队查询、协议签署、证书管理。

对应 DevEco Studio 中 AutoSigningConfigsService 的证书相关操作：
- get_team_id: 查询用户所属团队列表，取第一个 teamId
- sign_agreement: 签署开发者协议
- add_certificate: 上传 CSR 申请证书
- download_file: 获取下载 URL 并下载文件
- get_cert_list: 查询证书列表
"""

import logging
from typing import Any

import requests

from hapsign.api.client import HuaweiSignClient
from hapsign.config import (
    API_AGREEMENT,
    API_CERT_ADD,
    API_CERT_DELETE,
    API_CERT_LIST,
    API_REAPPLY_URL,
    API_USER_TEAM,
    BASE_URL,
    HEADER_ACCESS_TOKEN,
)

logger = logging.getLogger(__name__)


class CertAPI:
    """证书相关 API 封装。"""

    def __init__(self, client: HuaweiSignClient):
        """初始化。

        Args:
            client: 已认证的 HuaweiSignClient 实例。
        """
        self._client = client

    def get_team_id(self) -> str:
        """获取用户所属的第一个团队 ID。

        GET {API_USER_TEAM}，从响应中提取第一个团队的 id。

        Returns:
            团队 ID 字符串。

        Raises:
            ValueError: 未找到任何团队。
        """
        headers = self._client._get_headers()
        data = self._client._do_get(API_USER_TEAM, headers)
        teams = data.get("teams") or []
        if not teams:
            raise ValueError("未找到任何团队（teams 为空）")
        return str(teams[0].get("id", ""))

    def sign_agreement(self) -> bool:
        """签署未实名用户的开发者协议。

        POST {API_AGREEMENT}，header 中携带 accessToken。
        使用登录域名（BASE_URL），不是签名 API 域名。

        Returns:
            签署成功返回 True。
        """
        url = f"{BASE_URL}{API_AGREEMENT}"
        headers = {HEADER_ACCESS_TOKEN: self._client.access_token}
        resp = requests.post(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return True

    def add_certificate(
        self,
        csr_content: str,
        team_id: str,
        cert_name: str | None = None,
        req_source: str | None = None,
    ) -> bool:
        """上传 CSR 申请调试证书。

        POST form {API_CERT_ADD}
        参数：csr（CSR 文件内容）、certName（证书文件名）、certType=1（调试证书）。
        当 req_source 不为 None 时额外添加 reqSource 参数
        （Real Provision 路径需要 reqSource="IDE"）。

        cert/add 响应中 code 嵌套在 ret 字段下（如 {"ret":{"code":0}}），
        Java 通过 responseContent.contains('"code":0') 检查成功，
        因此用原始文本的子串匹配，而非 JSON 字段访问。

        下载证书需要先调用 get_cert_list 获取 certObjectId，
        再用 certObjectId 作为 sourceUrl 调用 reapply URL 接口。

        Args:
            csr_content: CSR 文件的文本内容。
            team_id: 团队 ID。
            cert_name: 证书名称，默认自动生成 "auto_debug_{teamId}.cer"。
            req_source: 请求来源标识，Real Provision 路径传 "IDE"。

        Returns:
            成功返回 True。

        Raises:
            RuntimeError: cert/add 响应不包含 "code":0。
        """
        if cert_name is None:
            cert_name = f"auto_debug_{team_id}.cer"

        headers = self._client._get_headers(team_id)
        params: dict[str, Any] = {
            "csr": csr_content,
            "certName": cert_name,
            "certType": "1",
        }
        if req_source is not None:
            params["reqSource"] = req_source
        text = self._client._do_post_form_text(API_CERT_ADD, headers, params)
        logger.debug("cert/add response: %s", text[:500])

        # Java 检查方式：responseContent.contains("\"code\":0")
        if '"code":0' not in text:
            raise RuntimeError(f"add_certificate failed: {text}")
        return True

    def get_cert_list(self, team_id: str) -> list[dict[str, Any]]:
        """查询证书列表。

        GET {API_CERT_LIST}（注意是 GET，不是 POST）。

        Args:
            team_id: 团队 ID。

        Returns:
            证书列表，每个元素为包含 certName、certObjectId、id 等字段的字典。
        """
        headers = self._client._get_headers(team_id)
        data = self._client._do_get(API_CERT_LIST, headers)
        return data.get("certList") or []

    def find_cert(self, team_id: str, cert_name: str | None = None) -> dict[str, Any]:
        """从证书列表中按名称查找证书信息。

        Args:
            team_id: 团队 ID。
            cert_name: 证书名称，默认 "auto_debug_{teamId}.cer"。

        Returns:
            证书信息字典，包含 certObjectId、id、certName 等字段。

        Raises:
            ValueError: 未找到匹配的证书。
        """
        if cert_name is None:
            cert_name = f"auto_debug_{team_id}.cer"
        cert_list = self.get_cert_list(team_id)
        for cert in cert_list:
            if cert.get("certName") == cert_name:
                return cert
        raise ValueError(f"Certificate not found in list: {cert_name}")

    def delete_certificate(self, team_id: str, cert_id: str) -> bool:
        """删除指定证书。

        DELETE {API_CERT_DELETE}，body JSON {\"certIds\": [cert_id]}。
        逆向自 AutoSigningConfigsService.deleteCertificate。

        响应中 code 嵌套在 ret 下，用字符串包含检查（与 Java 一致）。

        Args:
            team_id: 团队 ID。
            cert_id: 证书 ID（cert 列表中的 id 字段，不是 certObjectId）。

        Returns:
            删除成功返回 True。
        """
        headers = self._client._get_headers(team_id)
        text = self._client._do_delete_text(
            API_CERT_DELETE, headers, {"certIds": [cert_id]}
        )
        logger.debug("cert/delete response: %s", text[:500])
        return '"code":0' in text

    def download_file(
        self,
        source_url: str,
        team_id: str,
        save_path: str,
    ) -> bool:
        """通过 reapply URL 接口获取可下载的 URL，再下载证书/Profile 文件。

        POST form {API_REAPPLY_URL}，参数 sourceUrls={source_url}
        从响应提取新 URL 后下载内容并保存到本地文件。

        source_url 的值取决于文件类型：
        - 证书（.cer）：传 certObjectId（从 get_cert_list 获取）
        - Profile（.p7b）：传 provisionFileUrl（从 add_test_provision 响应获取）

        Args:
            source_url: 源标识（certObjectId 或 provisionFileUrl）。
            team_id: 团队 ID。
            save_path: 本地保存路径。

        Returns:
            下载成功返回 True。
        """
        # 第一步：获取可下载的重定向 URL
        headers = self._client._get_headers(team_id)
        params = {"sourceUrls": source_url}
        data = self._client._do_post_form(API_REAPPLY_URL, headers, params)

        urls_info = data.get("urlsInfo") or []
        if not urls_info:
            raise ValueError(f"reapply URL 返回为空: {data}")

        new_url = urls_info[0].get("newUrl", "")
        if not new_url:
            raise ValueError(f"reapply URL 中未找到 newUrl: {urls_info}")

        # 第二步：直接下载（不需要认证 header）
        resp = requests.get(new_url, timeout=60)
        resp.raise_for_status()

        with open(save_path, "wb") as f:
            f.write(resp.content)

        return True
