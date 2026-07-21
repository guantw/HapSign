"""HDC 工具封装 —— 获取设备 UDID 和安装 hap。"""

import re
import subprocess

from hapsign import config


class Installer:
    """使用 hdc 获取设备 UDID 并安装 hap 包。"""

    def get_udid(self) -> str:
        """获取已连接设备的 UDID。

        使用 ``hdc shell bm get -u`` 命令获取设备标识。
        输出形如::

            udid of current device is :
            5BC00B489B1B0A5A6687A1DD55918BC18FC75568AAEE07DF969B196F80DEBF46

        从输出中提取 64 位十六进制 UDID。

        Returns:
            UDID 字符串（64 位十六进制）。

        Raises:
            RuntimeError: 没有可用设备或无法获取 UDID 时抛出。
        """
        hdc = config.HDC_PATH
        commands = [
            [hdc, "shell", "bm", "get", "-u"],
            [hdc, "shell", "param", "get", "const.product.udid"],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0:
                # 从输出中提取 64 位十六进制 UDID
                match = re.search(r"\b([0-9A-Fa-f]{64})\b", result.stdout)
                if match:
                    return match.group(1)
        raise RuntimeError("无法获取设备 UDID: 请确认已连接设备且 hdc 可用")

    def install(self, hap_path: str) -> bool:
        """使用 hdc install 安装 hap 包到已连接设备。

        Args:
            hap_path: 本地 hap 文件路径。

        Returns:
            成功返回 True。

        Raises:
            RuntimeError: hdc install 执行失败时抛出。
        """
        hdc = config.HDC_PATH
        cmd = [hdc, "install", hap_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = (result.stdout or "") + (result.stderr or "")
        # hdc install 即使失败也可能返回 0，需要检查输出内容
        failed = "error:" in output.lower() or "failed" in output.lower()
        if result.returncode != 0 or failed:
            raise RuntimeError(
                f"hdc install 失败 (code={result.returncode}): {output.strip()}"
            )
        return True
