"""设备 API —— 调试设备注册与管理。

对应 AutoSigningConfigsService 中的设备操作：
- add_device: 注册调试设备（处理重复设备错误作为成功）
- get_device_list: 查询已注册设备列表
"""

from typing import Any

from hapsign.api.client import HuaweiSignClient
from hapsign.config import (
    API_DEVICE_ADD,
    API_DEVICE_LIST,
    ERR_DEVICE_DUPLICATE,
    ERR_DEVICE_UDID_DUPLICATE,
)

_DEVICE_ALREADY_EXISTS_CODES = {
    str(ERR_DEVICE_DUPLICATE),
    str(ERR_DEVICE_UDID_DUPLICATE),
}


def _is_device_already_exists(value: Any) -> bool:
    text = str(value)
    return any(code in text for code in _DEVICE_ALREADY_EXISTS_CODES)


def _extract_response_code(data: Any) -> Any:
    """从签名 API 响应提取业务 code（顶层或 ret 嵌套）。"""
    if not isinstance(data, dict):
        return None
    ret = data.get("ret")
    if isinstance(ret, dict) and "code" in ret:
        return ret.get("code")
    return data.get("code")


class DeviceAPI:
    """调试设备相关 API 封装。"""

    def __init__(self, client: HuaweiSignClient):
        """初始化。

        Args:
            client: 已认证的 HuaweiSignClient 实例。
        """
        self._client = client

    def add_device(
        self,
        udid: str,
        device_type: str,
        team_id: str,
        device_name: str | None = None,
    ) -> bool:
        """注册调试设备。

        POST form {API_DEVICE_ADD}
        参数：deviceName（自动生成）、udid（设备唯一标识）、deviceType（设备类型码）。

        设备名称重复（205389857）或 UDID 已注册（205389858）视为成功，
        后续从设备列表复用已有记录。

        Args:
            udid: 设备 UDID（64 位十六进制字符串）。
            device_type: 设备类型码（如 "4" 表示手机，参见 config.DEVICE_TYPE_*）。
            team_id: 团队 ID。
            device_name: 设备显示名，默认使用随机数和时间戳自动生成。

        Returns:
            注册成功（含设备已存在）返回 True。
        """
        if device_name is None:
            import random
            import time

            device_name = (
                f"auto_sign_device_No."
                f"{random.randint(0, 2999)}{int(time.time() * 1000)}"
            )

        headers = self._client._get_headers(team_id)
        params: dict[str, str] = {
            "deviceName": device_name,
            "udid": udid,
            "deviceType": device_type,
        }
        try:
            data = self._client._do_post_form(API_DEVICE_ADD, headers, params)
        except Exception as exc:
            # HTTP 层失败时仍兼容逆向场景：错误信息里可能直接带业务码
            if _is_device_already_exists(exc):
                return True
            raise

        # 200 响应也可能返回业务错误码（与 DevEco 行为一致）
        code = _extract_response_code(data)
        if code in (None, 0, "0"):
            return True
        if _is_device_already_exists(code):
            return True
        raise RuntimeError(f"add_device failed: {data}")

    def get_device_list(self, team_id: str) -> list[dict[str, Any]]:
        """查询已注册的设备列表。

        GET {API_DEVICE_LIST}（注意是 GET，不是 POST）。
        参数：encodeFlag=0, start=1, pageSize=100。

        Args:
            team_id: 团队 ID。

        Returns:
            设备列表，每个元素包含 id、udid、deviceName、deviceType 等字段。
        """
        headers = self._client._get_headers(team_id)
        params = {"encodeFlag": "0", "start": "1", "pageSize": "100"}
        data = self._client._do_get(API_DEVICE_LIST, headers, params)
        return data.get("list") or []

    def find_device_id(self, team_id: str, udid: str) -> str:
        """从设备列表中按 UDID 查找设备 ID。

        Args:
            team_id: 团队 ID。
            udid: 设备 UDID。

        Returns:
            设备 ID 字符串。

        Raises:
            ValueError: 未找到匹配的设备。
        """
        device_list = self.get_device_list(team_id)
        for device in device_list:
            if device.get("udid") == udid:
                return str(device.get("id", ""))
        raise ValueError(f"Device not found in list: {udid}")
