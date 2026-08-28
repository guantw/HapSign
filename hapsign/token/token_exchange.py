"""Token 交换模块 —— tempToken → JWT → accessToken。

从 DevEco Studio 登录流程逆向获得：
1. 浏览器回调拿到 tempToken
2. GET temptoken/check → 返回 jwtToken（字符串）
3. GET jwToken/check, header jwtToken → 返回 accessToken / refreshToken
4. JWT 中段 Base64 解码得到 userName / userId
"""

import base64
import json
import logging
import threading
from urllib.parse import urlparse

import requests

from hapsign.cancellation import raise_if_cancelled
from hapsign.config import (
    APP_ID,
    BASE_URL,
    HEADER_JWT_TOKEN,
    HEADER_REFRESH,
    JWT_TOKEN_CHECK_PATH,
    LOGIN_PROTOCOL_VERSION,
    TEMP_TOKEN_CHECK_PATH,
)
from hapsign.diagnostics import sensitive_logging_enabled
from hapsign.models import TokenInfo

logger = logging.getLogger(__name__)


def _log_response(
    operation: str,
    url: str,
    response: requests.Response,
    *,
    request_data: object,
) -> None:
    parsed = urlparse(url)
    content = getattr(response, "content", b"")
    response_size = len(content) if isinstance(content, (bytes, str)) else -1
    logger.debug(
        "[token] %s response=%s://%s%s status=%d bytes=%d",
        operation,
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        response.status_code,
        response_size,
    )
    if sensitive_logging_enabled():
        logger.debug(
            "[token] sensitive %s request=%r response=%r",
            operation,
            request_data,
            response.text,
        )


class TokenExchange:
    """Token 交换工具类。"""

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self.cancel_event = cancel_event

    def _check_cancelled(self) -> None:
        raise_if_cancelled(self.cancel_event)

    def exchange_temp_token(
        self,
        temp_token: str,
        site: str = "CN",
        version: str = LOGIN_PROTOCOL_VERSION,
    ) -> str:
        """用 tempToken 换取 jwtToken。

        Args:
            temp_token: 浏览器回调获得的临时令牌。
            site: 站点代码（CN / SG / DE / RU）。
            version: 登录协议版本（默认取 config.LOGIN_PROTOCOL_VERSION）。

        Returns:
            jwtToken 字符串。

        Raises:
            requests.RequestException: 网络或服务端异常。
        """
        url = f"{BASE_URL}/{TEMP_TOKEN_CHECK_PATH}"
        params = {
            "tempToken": temp_token,
            "site": site,
            "version": version,
            "appid": APP_ID,
        }
        self._check_cancelled()
        resp = requests.get(url, params=params, timeout=(5, 15))
        self._check_cancelled()
        _log_response("temp-token-check", url, resp, request_data=params)
        resp.raise_for_status()
        # 响应正文即为 jwtToken 字符串（非 JSON）
        return resp.text

    def get_access_token(self, jwt_token: str) -> TokenInfo:
        """用 jwtToken 换取 accessToken / refreshToken，并解码用户信息。

        Args:
            jwt_token: 从 exchange_temp_token 获得的 JWT 令牌。

        Returns:
            TokenInfo 对象，包含 access_token、refresh_token、user_id、user_name 等。

        Raises:
            ValueError: 响应状态为 false 或 JWT 解码失败。
            requests.RequestException: 网络或服务端异常。
        """
        url = f"{BASE_URL}/{JWT_TOKEN_CHECK_PATH}"
        headers = {HEADER_JWT_TOKEN: jwt_token, HEADER_REFRESH: "false"}
        self._check_cancelled()
        resp = requests.get(url, headers=headers, timeout=(5, 15))
        self._check_cancelled()
        _log_response("jwt-token-check", url, resp, request_data=headers)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("status"):
            raise ValueError(f"get_access_token 返回 status=false: {data}")

        user_info = data.get("userInfo", {})
        access_token = user_info.get("accessToken", "")
        refresh_token = user_info.get("refreshToken", "")
        national_code = user_info.get("nationalCode", "")
        real_name = user_info.get("realName", False)

        # 从 JWT 中段 Base64 解码得到 userName / userId
        user_id = ""
        user_name = ""
        try:
            payload_b64 = jwt_token.split(".")[1]
            # Base64 URL-safe 补足 padding
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            user_name = payload.get("userName", "")
            user_id = payload.get("userId", "")
        except (IndexError, ValueError, json.JSONDecodeError) as e:
            raise ValueError(f"JWT 中段解码失败: {e}") from e

        return TokenInfo(
            access_token=access_token,
            refresh_token=refresh_token,
            user_id=user_id,
            user_name=user_name,
            national_code=national_code,
            real_name=bool(real_name),
            jwt_token=jwt_token,
        )

    def refresh_access_token(self, jwt_token: str) -> str:
        """刷新 accessToken（同 jwToken/check 接口，header refresh=true）。

        Args:
            jwt_token: 之前获得的 JWT 令牌。

        Returns:
            新的 accessToken 字符串。

        Raises:
            ValueError: 响应状态为 false。
            requests.RequestException: 网络或服务端异常。
        """
        url = f"{BASE_URL}/{JWT_TOKEN_CHECK_PATH}"
        headers = {HEADER_JWT_TOKEN: jwt_token, HEADER_REFRESH: "true"}
        self._check_cancelled()
        resp = requests.get(url, headers=headers, timeout=(5, 15))
        self._check_cancelled()
        _log_response("refresh-token", url, resp, request_data=headers)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("status"):
            raise ValueError(f"refresh_access_token 返回 status=false: {data}")

        user_info = data.get("userInfo", {})
        return user_info.get("accessToken", "")
