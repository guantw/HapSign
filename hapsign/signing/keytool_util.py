"""keytool 工具封装 —— 生成密钥对和 CSR。"""

import os
import subprocess

from hapsign import config


class KeytoolUtil:
    """封装 keytool 命令，用于生成 EC 密钥对和 CSR。"""

    @staticmethod
    def _get_keytool_path() -> str:
        """从 DEVECO_JBR 路径推导 keytool.exe 路径。"""
        jbr_dir = os.path.dirname(config.DEVECO_JBR)
        return os.path.join(jbr_dir, "keytool.exe")

    def generate_keypair(
        self,
        keystore_path: str,
        alias: str = config.KEY_ALIAS,
        password: str = "123456",
    ) -> bool:
        """使用 keytool -genkeypair 生成 EC 256 密钥对。

        如果 keystore 文件已存在则先删除，确保可重复执行。

        Args:
            keystore_path: 输出的 .p12 密钥库路径。
            alias: 密钥别名，默认 config.KEY_ALIAS。
            password: 密钥库和密钥密码。

        Returns:
            成功返回 True。

        Raises:
            RuntimeError: keytool 执行失败时抛出。
        """
        # 删除已有 keystore（避免别名冲突）
        if os.path.exists(keystore_path):
            os.remove(keystore_path)

        keytool = self._get_keytool_path()
        cmd = [
            keytool,
            "-genkeypair",
            "-alias",
            alias,
            "-keyalg",
            config.KEY_ALG,
            "-keysize",
            config.KEY_SIZE,
            "-validity",
            str(config.KEY_VALIDITY_DAYS),
            "-keystore",
            keystore_path,
            "-storepass",
            password,
            "-dname",
            config.KEY_DNAME,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"keytool -genkeypair 失败 (code={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        return True

    def generate_csr(
        self,
        keystore_path: str,
        alias: str = config.KEY_ALIAS,
        password: str = "123456",
        csr_path: str = "",
    ) -> str:
        """使用 keytool -certreq 生成 CSR 并返回其内容。

        Args:
            keystore_path: .p12 密钥库路径。
            alias: 密钥别名，默认 config.KEY_ALIAS。
            password: 密钥库密码。
            csr_path: 输出的 .csr 文件路径。

        Returns:
            CSR 文件内容字符串。

        Raises:
            RuntimeError: keytool 执行失败或读取 CSR 文件失败时抛出。
        """
        keytool = self._get_keytool_path()
        cmd = [
            keytool,
            "-certreq",
            "-alias",
            alias,
            "-keystore",
            keystore_path,
            "-storepass",
            password,
            "-file",
            csr_path,
            "-sigalg",
            config.SIGN_ALG,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"keytool -certreq 失败 (code={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        try:
            with open(csr_path, encoding="utf-8") as f:
                return f.read()
        except OSError as e:
            raise RuntimeError(f"无法读取生成的 CSR 文件 {csr_path}: {e}") from e
