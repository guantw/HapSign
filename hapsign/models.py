"""数据模型。"""

from dataclasses import dataclass


@dataclass
class TokenInfo:
    """登录后获得的 token 信息。"""

    access_token: str = ""
    refresh_token: str = ""
    user_id: str = ""
    user_name: str = ""
    national_code: str = ""
    real_name: bool = False
    jwt_token: str = ""


@dataclass
class CertResult:
    """上传 CSR 后获得的证书信息。"""

    cert_object_id: str = ""
    cert_id: str = ""


@dataclass
class AppBriefInfo:
    """应用简要信息（从 app.info 接口获取）。

    用于 Real Provision 路径：需要 appId 和 projectId。
    pureFlag=1 表示是 HarmonyOS 应用。
    """

    app_id: str = ""
    project_id: str = ""
    pure_flag: int = 0
    bundle_name: str = ""


@dataclass
class ProvisionResult:
    """创建 Profile 后获得的结果。"""

    provision_file_url: str = ""
    provision_id: str = ""
    provision_name: str = ""
