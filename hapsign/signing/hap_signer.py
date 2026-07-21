"""hap-sign-tool 封装 —— 对 hap 包签名。"""

import subprocess

from hapsign import config


class HapSigner:
    """使用 hap-sign-tool.jar 对 hap 包签名。"""

    def sign_hap(
        self,
        hap_path: str,
        cer_path: str,
        p7b_path: str,
        p12_path: str,
        alias: str = config.KEY_ALIAS,
        password: str = "123456",
        output_path: str = "",
    ) -> bool:
        """使用 hap-sign-tool sign-app 命令签名 hap。

        Args:
            hap_path: 未签名的 hap 文件路径。
            cer_path: 证书文件路径 (.cer)。
            p7b_path: Profile 文件路径 (.p7b)。
            p12_path: 密钥库文件路径 (.p12)。
            alias: 密钥别名，默认 config.KEY_ALIAS。
            password: 密钥库/密钥密码。
            output_path: 签名后输出 hap 路径。

        Returns:
            成功返回 True。

        Raises:
            RuntimeError: hap-sign-tool 执行失败时抛出。
        """
        java = config.DEVECO_JBR
        hap_sign_tool = config.HAP_SIGN_TOOL
        cmd = [
            java,
            "-jar",
            hap_sign_tool,
            "sign-app",
            "-mode",
            "localSign",
            "-keyAlias",
            alias,
            "-keyPwd",
            password,
            "-appCertFile",
            cer_path,
            "-profileFile",
            p7b_path,
            "-inFile",
            hap_path,
            "-signAlg",
            config.HAP_SIGN_ALG,
            "-keystoreFile",
            p12_path,
            "-keystorePwd",
            password,
            "-outFile",
            output_path,
            "-compatibleVersion",
            config.HAP_COMPATIBLE_VERSION,
            "-signCode",
            "1",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"hap-sign-tool sign-app 失败 (code={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return True
