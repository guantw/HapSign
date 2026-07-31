"""配置常量 —— 域名、SDK 路径、API 路径、密钥参数。
所有逆向获得的真实值集中在此，便于维护。"""

import os
import sys

# ── 域名 ──────────────────────────────────────────────────────
# 登录/认证域名（从 IdeSystem.properties 逆向获得）
BASE_URL = "https://devecostudio.huawei.com"

# 签名 API 域名（从 GrsAddress.java + GRS 缓存获得）
# SPACE_CLOUD → KEY_AGC_SIGN("agcsign") → connect-api.cloud.huawei.com
# AGC_UPS → KEY_AGC_UPS("agcups") → connect-api.cloud.huawei.com
# 注意：deveco-drcn.op.hicloud.com 是旧域名，DNS 已不解析，实际使用 connect-api
CLOUD_BASE_URL = "https://connect-api.cloud.huawei.com"

# ── 登录端点（从 IdeSystem.properties 逆向获得）──────────────
LOGIN_AUTH_PATH = "console/DevEcoIDE/apply"
LOGIN_SUCCESS_PATH = "console/DevEcoIDE/loginSuccess"
TEMP_TOKEN_CHECK_PATH = "authrouter/auth/api/temptoken/check"
JWT_TOKEN_CHECK_PATH = "authrouter/auth/api/jwToken/check"

# ── 签名 API 端点（从 SignatureMgmt.properties 逆向获得）─────
API_USER_TEAM = "/api/ups/user-permission-service/v1/user-team-list"
API_CERT_LIST = "/api/cps/harmony-cert-manage/v1/cert/list"
API_CERT_ADD = "/api/cps/harmony-cert-manage/v1/cert/add"
API_CERT_DELETE = "/api/cps/harmony-cert-manage/v1/cert/delete"
API_DEVICE_LIST = "/api/cps/device-manage/v1/device/list"
API_DEVICE_ADD = "/api/cps/device-manage/v1/device/add"
API_PROVISION_LIST = "/api/cps/provision-manage/v1/provision/list"
API_TEST_PROVISION_ADD = "/api/cps/provision-manage/v1/ide/test/provision/add"
API_REAL_PROVISION_ADD = "/api/cps/provision-manage/v1/provision/add"
API_PROVISION_DELETE = "/api/cps/provision-manage/v1/provision/delete"
API_APP_BRIEF_INFO = "/api/amis/app-manage/v1/manage/app/brief-info/list"
API_AGGREGATE_INFO = "/api/cpms/project-service/v1/services-aggregate-info"
API_REAPPLY_URL = "/api/amis/app-manage/v1/objects/url/reapply"
API_AGREEMENT = "/authrouter/unrealname/agreement"

# ── 登录参数 ──────────────────────────────────────────────────
APP_ID = "1007"

# 国家码映射（从 HiAiLoginService 逆向获得）
# siteId → countryCode: 1→CN, 5→SG, 7→DE, 8→RU
SITE_ID_MAP = {"CN": "1", "SG": "5", "DE": "7", "RU": "8"}

# ── keytool / 密钥参数（从 SigningConfigsUtil 逆向获得）───────
KEY_ALG = "EC"
KEY_SIZE = "256"
KEY_VALIDITY_DAYS = 9125  # 25 年
KEY_ALIAS = "debugKey"
KEY_DNAME = "CN=DebugKey,OU=,O=,L=,ST=,C="
SIGN_ALG = "SHA256withECDSA"
# 仅用于本机调试密钥库。正式环境应通过环境变量覆盖。
KEYSTORE_PASSWORD = os.environ.get("HAPSIGN_KEYSTORE_PASSWORD", "123456")

# ── hap 签名 ──────────────────────────────────────────────────
HAP_SIGN_ALG = "SHA256withECDSA"
HAP_COMPATIBLE_VERSION = "10"  # min compatible api version

# ── 设备类型映射（从 coverDeviceType 逆向获得）──────────────
DEVICE_TYPE_PHONE = "4"
DEVICE_TYPE_WEARABLE = "2"
DEVICE_TYPE_TV = "8"
DEVICE_TYPE_ROUTER = "9"
DEVICE_TYPE_LITE_WEARABLE = "1"

# ── 错误码（从 CommonConstants 逆向获得）─────────────────────
ERR_NOT_HARMONY_USER = 205389904
ERR_CERT_EXCEED_LIMIT = 205389872
ERR_DEVICE_EXCEED_LIMIT = 205389859
ERR_DEVICE_DUPLICATE = 205389857
ERR_PROVISION_EXCEED_LIMIT = 205389938
ERR_PROVISION_EXCEED_LIMIT_2 = 205389845
ERR_APP_ID_INVALID = 205389959
ERR_TOKEN_INVALID_CODE = 4000  # 响应 code=4000 表示 token 失效


# ── 本机 SDK 路径（可通过环境变量覆盖）──────────────────────
def default_deveco_home(platform: str | None = None) -> str:
    """返回当前平台的 DevEco Studio 默认安装根目录。"""
    plat = sys.platform if platform is None else platform
    if plat == "darwin":
        return "/Applications/DevEco-Studio.app/Contents"
    if plat == "win32":
        return r"D:\Program Files\Huawei\DevEco Studio"
    return "/opt/DevEco-Studio"


def resolve_sdk_paths(
    deveco_home: str | None = None,
    platform: str | None = None,
) -> tuple[str, str, str, str]:
    """解析 java / hap-sign-tool / hdc / keytool 路径。

    Returns:
        (java, hap_sign_tool, hdc, keytool)
    """
    plat = sys.platform if platform is None else platform
    home = deveco_home if deveco_home is not None else default_deveco_home(plat)
    toolchains = os.path.join(home, "sdk", "default", "openharmony", "toolchains")
    hap_sign_tool = os.path.join(toolchains, "lib", "hap-sign-tool.jar")

    if plat == "darwin":
        bin_dir = os.path.join(home, "jbr", "Contents", "Home", "bin")
        java = os.path.join(bin_dir, "java")
        keytool = os.path.join(bin_dir, "keytool")
        hdc = os.path.join(toolchains, "hdc")
    elif plat == "win32":
        bin_dir = os.path.join(home, "jbr", "bin")
        java = os.path.join(bin_dir, "java.exe")
        keytool = os.path.join(bin_dir, "keytool.exe")
        hdc = os.path.join(toolchains, "hdc.exe")
    else:
        bin_dir = os.path.join(home, "jbr", "bin")
        java = os.path.join(bin_dir, "java")
        keytool = os.path.join(bin_dir, "keytool")
        hdc = os.path.join(toolchains, "hdc")

    return java, hap_sign_tool, hdc, keytool


_DEVECO_HOME = os.environ.get("DEVECO_HOME") or default_deveco_home()
DEVECO_JBR, HAP_SIGN_TOOL, HDC_PATH, KEYTOOL_PATH = resolve_sdk_paths(_DEVECO_HOME)

# ── HTTP header 常量 ──────────────────────────────────────────
HEADER_OAUTH2_TOKEN = "oauth2Token"
HEADER_UID = "uid"
HEADER_TEAM_ID = "teamId"
HEADER_ACCESS_TOKEN = "accessToken"
HEADER_JWT_TOKEN = "jwtToken"
HEADER_REFRESH = "refresh"
HEADER_USER_AGENT = "Chrome/49.0.2623.75"
HEADER_ACCEPT_LANG = "zh-CN"

# ── ACL 权限白名单（从 DevEco 6.1 生成的 p7b allowed-acls 提取）────
# 仅这些权限可通过 aclPermissionList 预授权，其余权限会被服务端拒绝
# （"exist permission not in support scope"）
ACL_PERMISSION_WHITELIST = frozenset(
    {
        "ohos.permission.READ_WRITE_DESKTOP_DIRECTORY",
        "ohos.permission.kernel.ALLOW_WRITABLE_CODE_MEMORY",
        "ohos.permission.READ_WRITE_DOCUMENTS_DIRECTORY",
        "ohos.permission.CUSTOM_SANDBOX",
        "ohos.permission.READ_PASTEBOARD",
        "ohos.permission.READ_WRITE_USER_FILE",
        "ohos.permission.ALLOW_EXTERNAL_NATIVE_CODE",
        "ohos.permission.READ_WRITE_DOWNLOAD_DIRECTORY",
        # 以下权限从 5.x 白名单保留，兼容旧 SDK
        "ohos.permission.READ_CONTACTS",
        "ohos.permission.WRITE_CONTACTS",
        "ohos.permission.READ_AUDIO",
        "ohos.permission.WRITE_AUDIO",
        "ohos.permission.READ_IMAGEVIDEO",
        "ohos.permission.WRITE_IMAGEVIDEO",
        "ohos.permission.ACCESS_DDK_USB",
        "ohos.permission.ACCESS_DDK_HID",
        "ohos.permission.SYSTEM_FLOAT_WINDOW",
        "ohos.permission.FILE_ACCESS_PERSIST",
        "ohos.permission.INPUT_MONITORING",
        "ohos.permission.INTERCEPT_INPUT_EVENT",
        "ohos.permission.SHORT_TERM_WRITE_IMAGEVIDEO",
    }
)
