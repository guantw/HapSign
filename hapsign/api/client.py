"""HTTP 客户端基类 —— 封装华为签名 API 的认证头和请求方法。

从 RequestAdapter / AutoSigningConfigsService 逆向获得：
- 认证 header：oauth2Token + uid (+ teamId)
- POST form-encoded 大多数操作
- POST JSON 用于 profile 创建
- code=4000 或 HTTP 401 表示 token 失效
"""

import logging
import threading
from typing import Any
from urllib.parse import urlparse

import requests

from hapsign.cancellation import raise_if_cancelled
from hapsign.config import (
    ACCEPT_LANGUAGE,
    BASE_URL,
    CLOUD_BASE_URL,
    ERR_TOKEN_INVALID_CODE,
    HEADER_ACCEPT_LANGUAGE,
    HEADER_OAUTH2_TOKEN,
    HEADER_TEAM_ID,
    HEADER_UID,
    HEADER_USER_AGENT,
    USER_AGENT,
)
from hapsign.diagnostics import sensitive_logging_enabled

logger = logging.getLogger(__name__)


class TokenExpiredError(Exception):
    """Token 失效异常：HTTP 401 或响应 code=4000 时抛出。"""


class HuaweiSignClient:
    """华为签名 API 的 HTTP 客户端。

    给每个请求自动带上 oauth2Token、uid 等认证 header。
    """

    def __init__(
        self,
        access_token: str,
        uid: str,
        base_url: str = CLOUD_BASE_URL,
        cancel_event: threading.Event | None = None,
    ):
        """初始化客户端。

        Args:
            access_token: 从 TokenExchange.get_access_token 获得的 accessToken。
            uid: 从 JWT 解码获得的 userId。
            base_url: 签名 API 域名，默认 connect-api.cloud.huawei.com。
        """
        self.access_token = access_token
        self.uid = uid
        self.base_url = base_url.rstrip("/")
        self.cancel_event = cancel_event

    def _check_cancelled(self) -> None:
        raise_if_cancelled(self.cancel_event)

    def _get_headers(self, team_id: str | None = None) -> dict[str, str]:
        """构造请求头。

        Args:
            team_id: 团队 ID（可选），部分接口需要。

        Returns:
            包含认证信息的 header 字典。
        """
        headers = {
            HEADER_OAUTH2_TOKEN: self.access_token,
            HEADER_UID: self.uid,
            HEADER_USER_AGENT: USER_AGENT,
            HEADER_ACCEPT_LANGUAGE: ACCEPT_LANGUAGE,
        }
        if team_id is not None:
            headers[HEADER_TEAM_ID] = team_id
        return headers

    def _build_url(self, path: str) -> str:
        """拼接完整 URL，根据 API 路径前缀自动选择域名。

        路由规则（从 CommonUtil.getCloudRequestUrl / getAgcUpsRequestUrl 逆向）：
        - /authrouter/ → BASE_URL（登录/认证 API: devecostudio.huawei.com）
        - 其他        → CLOUD_BASE_URL（签名 API: connect-api.cloud.huawei.com）
        """
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if path.startswith("/authrouter/"):
            return f"{BASE_URL}{path}"
        return f"{self.base_url}{path}"

    def _check_response(self, resp: requests.Response) -> None:
        """检查响应中的 token 失效标记，抛出 TokenExpiredError。

        签名 API 的响应结构为 {"ret": {"code": 0, ...}}，code 嵌套在 ret 下；
        认证 API 的响应结构为 {"code": 0, ...}，code 在顶层。两种都检查。
        """
        if resp.status_code == 401:
            phrase = resp.reason or ""
            if "accessToken invalid" in phrase or "getTokenInfo return null" in phrase:
                raise TokenExpiredError(f"Token 失效（HTTP 401）: {phrase}")
            raise TokenExpiredError(f"HTTP 401: {phrase}")

        # 部分接口在 200 响应中返回 code=4000 表示 token 失效
        try:
            data = resp.json()
            if isinstance(data, dict):
                if data.get("code") == ERR_TOKEN_INVALID_CODE:
                    raise TokenExpiredError(
                        f"Token 失效（code=4000）: {data.get('message', '')}"
                    )
                ret = data.get("ret")
                if isinstance(ret, dict) and ret.get("code") == ERR_TOKEN_INVALID_CODE:
                    raise TokenExpiredError(
                        f"Token 失效（ret.code=4000）: {ret.get('msg', '')}"
                    )
        except (ValueError, AttributeError):
            pass

    def _log_http(
        self,
        method: str,
        url: str,
        response: requests.Response,
        *,
        headers: dict[str, str],
        payload: object = None,
    ) -> None:
        """记录足以定位接口变更的元数据；完整载荷由敏感开关控制。"""
        parsed = urlparse(url)
        response_keys: list[str] = []
        business_code: object = None
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                response_keys = sorted(str(key) for key in decoded)
                ret = decoded.get("ret")
                business_code = (
                    ret.get("code") if isinstance(ret, dict) else decoded.get("code")
                )
        except (ValueError, AttributeError):
            pass
        content = getattr(response, "content", b"")
        response_size = len(content) if isinstance(content, (bytes, str)) else -1
        logger.debug(
            "[api] %s %s://%s%s status=%d bytes=%d keys=%s code=%r",
            method,
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            response.status_code,
            response_size,
            response_keys,
            business_code,
        )
        if sensitive_logging_enabled():
            logger.debug(
                "[api] sensitive %s %s headers=%r payload=%r response=%r",
                method,
                url,
                headers,
                payload,
                response.text,
            )

    def _do_get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """执行 GET 请求并返回解析后的 JSON。"""
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.get(full_url, headers=headers, params=params, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("GET", full_url, resp, headers=headers, payload=params)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.json()

    def _do_post_form(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行 POST form-encoded 请求并返回解析后的 JSON。"""
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.post(full_url, headers=headers, data=params, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("POST", full_url, resp, headers=headers, payload=params)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.json()

    def _do_post_json(
        self,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行 POST JSON 请求并返回解析后的 JSON。"""
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.post(full_url, headers=headers, json=data, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("POST", full_url, resp, headers=headers, payload=data)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.json()

    def _do_delete(
        self,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行 DELETE 请求（带 JSON body）并返回解析后的 JSON。"""
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.delete(full_url, headers=headers, json=data, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("DELETE", full_url, resp, headers=headers, payload=data)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.json()

    def _do_post_form_text(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> str:
        """执行 POST form-encoded 请求并返回原始响应文本。

        用于 cert/add 等接口：code 嵌套在 ret 字段下，
        Java 通过 responseContent.contains('"code":0') 检查成功。
        """
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.post(full_url, headers=headers, data=params, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("POST", full_url, resp, headers=headers, payload=params)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.text

    def _do_delete_text(
        self,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> str:
        """执行 DELETE 请求（带 JSON body）并返回原始响应文本。"""
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.delete(full_url, headers=headers, json=data, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("DELETE", full_url, resp, headers=headers, payload=data)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.text

    def _do_post_json_text(
        self,
        url: str,
        headers: dict[str, str],
        data: dict[str, Any] | None = None,
    ) -> str:
        """执行 POST JSON 请求并返回原始响应文本。

        用于 provision/add 等接口：code 嵌套在 ret 字段下，
        Java 通过 responseContent.contains('"code":0') 检查成功。
        """
        full_url = self._build_url(url)
        self._check_cancelled()
        resp = requests.post(full_url, headers=headers, json=data, timeout=(5, 15))
        self._check_cancelled()
        self._log_http("POST", full_url, resp, headers=headers, payload=data)
        self._check_response(resp)
        resp.raise_for_status()
        return resp.text
