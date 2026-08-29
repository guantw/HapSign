"""登录、token 交换、签名材料申请、HAP 签名和安装的全流程编排。

双重缓存策略（同一天内复用，避免反复登录和申请）：
  1. Token 缓存：``~/.hapsign/.token_cache.json`` 存储当天登录的 token 信息，
     同账号同一天内复用，不重新登录。
  2. 签名文件缓存：``~/.hapsign/{bundle_name}/metadata.json`` 存储当天申请的
     签名文件路径，同一天内复用，不重新申请证书/设备/Profile。

缓存失效场景：
  - 跨天：token 和签名文件缓存都失效，重新登录 + 重新申请。
  - token 过期：缓存 token 用不了时自动刷新；刷新也失败则回退到重新登录。
  - 签名文件缺失：重新申请（用缓存 token，不重新登录）。
"""

import json
import logging
import os
import shutil
import tempfile
import threading
import traceback
import uuid
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

from hapsign.api.capability_api import CapabilityAPI
from hapsign.api.cert_api import CertAPI
from hapsign.api.client import HuaweiSignClient, TokenExpiredError
from hapsign.api.device_api import DeviceAPI
from hapsign.api.provision_api import ProvisionAPI
from hapsign.cancellation import OperationCancelled, raise_if_cancelled
from hapsign.config import (
    ACL_PERMISSION_WHITELIST,
    DEVICE_TYPE_PHONE,
    KEY_ALIAS,
    KEYSTORE_PASSWORD,
)
from hapsign.diagnostics import is_valid_device_udid, redact_sensitive_text
from hapsign.login.browser_login import BrowserLogin
from hapsign.models import AppBriefInfo, CertResult, ProvisionResult, TokenInfo
from hapsign.signing.hap_inspect import is_hap_signed
from hapsign.signing.hap_signer import HapSigner
from hapsign.signing.installer import Installer
from hapsign.signing.keytool_util import KeytoolUtil
from hapsign.token import secure_token_cache
from hapsign.token.token_exchange import TokenExchange

logger = logging.getLogger(__name__)

# 默认签名状态目录。Path.home() 在 macOS/Linux 解析为 $HOME，在 Windows
# 解析为 %USERPROFILE%，因此不会依赖调用命令时的工作目录。
DEFAULT_STATE_DIR_NAME = ".hapsign"


def default_state_dir() -> str:
    """返回跨平台的默认 Token 与签名材料目录。"""
    return str((Path.home() / DEFAULT_STATE_DIR_NAME).resolve())


# 保留旧常量名，避免外部调用方导入时报错；新代码应调用 default_state_dir()。
SIGNING_FILES_DIR = default_state_dir()
SIGNED_HAP_MANIFEST = ".hapsign-signed-haps.json"


class SignPipeline:
    """签名 + 安装全流程编排。

    使用示例::

        pipeline = SignPipeline(
            hap_path="app.hap",
            bundle_name="com.example.myapp",
        )
        pipeline.run()

    生命周期约定：SignPipeline 是一次性对象，一个实例只应调用一次 ``run()``；
    再次执行请新建实例。运行期状态（_team_id、_app_info、_cert_result、
    签名材料路径等）在构造函数中显式初始化。
    """

    def __init__(
        self,
        hap_path: str,
        bundle_name: str,
        country: str = "CN",
        device_type: str = DEVICE_TYPE_PHONE,
        work_dir: str = "",
        state_dir: str = "",
        enable_capability: bool = False,
        force_refresh_token: bool = False,
        force_refresh_signing: bool = False,
        browser_mode: str = "system",
        signed_output_dir: str = "",
        keep_signed_hap: bool = True,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
        serial: str | None = None,
        install_after_sign: bool = True,
        *,
        device_udid: str = "",
        signed_output_path: str = "",
        overwrite_output: bool = False,
    ):
        self.hap_path = hap_path
        self.bundle_name = bundle_name
        self.country = country
        self.device_type = device_type
        self.serial = serial
        self.requested_enable_capability = bool(enable_capability)
        self.enable_capability = self.requested_enable_capability
        self.force_refresh_token = force_refresh_token
        self.force_refresh_signing = force_refresh_signing
        self.browser_mode = browser_mode
        self.keep_signed_hap = keep_signed_hap
        self.install_after_sign = install_after_sign
        self.device_udid = device_udid.strip()
        if self.device_udid and not is_valid_device_udid(self.device_udid):
            raise ValueError("设备 UDID 必须是 64 位十六进制字符串")
        self.signed_output_path = (
            os.path.expanduser(signed_output_path) if signed_output_path else ""
        )
        self.overwrite_output = overwrite_output
        if self.signed_output_path:
            output_absolute = os.path.realpath(os.path.abspath(self.signed_output_path))
            input_absolute = os.path.realpath(os.path.abspath(self.hap_path))
            if os.path.normcase(output_absolute) == os.path.normcase(input_absolute):
                raise ValueError("签名输出不能覆盖输入 HAP，请指定其他路径")
            if os.path.splitext(output_absolute)[1].lower() != ".hap":
                raise ValueError("签名输出路径必须以 .hap 结尾")
        self._temporary_signed_dir: tempfile.TemporaryDirectory | None = None
        self.cancel_event = cancel_event
        self.progress_callback = progress_callback
        self.state_dir = (
            os.path.abspath(os.path.expanduser(state_dir))
            if state_dir
            else default_state_dir()
        )
        if work_dir:
            self.work_dir = os.path.abspath(os.path.expanduser(work_dir))
        else:
            self.work_dir = os.path.join(self.state_dir, bundle_name)
        self.signed_output_dir = (
            os.path.abspath(os.path.expanduser(signed_output_dir))
            if signed_output_dir
            else self.work_dir
        )
        os.makedirs(self.work_dir, mode=0o700, exist_ok=True)
        self.keystore_password = KEYSTORE_PASSWORD
        self._metadata_path = os.path.join(self.work_dir, "metadata.json")
        self._token_cache_path = os.path.join(self.state_dir, ".token_cache.json")

        self._token_exchange = TokenExchange(cancel_event=cancel_event)
        self._token_info: TokenInfo | None = None
        self._client: HuaweiSignClient | None = None
        self._cert_api: CertAPI | None = None
        self._device_api: DeviceAPI | None = None
        self._provision_api: ProvisionAPI | None = None
        self._capability_api: CapabilityAPI | None = None
        self._token_from_cache = False
        self._udid = self.device_udid
        self._installer: Installer | None = None

        # 运行期状态：全部显式初始化，避免 hasattr 探测和未初始化属性。
        self._team_id = ""
        self._app_info: AppBriefInfo | None = None
        self._cert_result: CertResult | None = None
        self._p12_path = ""
        self._cer_path = ""
        self._p7b_path = ""
        self._csr_path = ""
        self._csr_content = ""
        self._signed_hap_path = ""
        self._last_error = ""

    def _check_cancelled(self) -> None:
        raise_if_cancelled(self.cancel_event)

    def _emit_progress(self, value: int, label: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(value, label)

    # ── Token 缓存 ──────────────────────────────────────────────

    def _load_token_cache(self) -> dict | None:
        """加载当天的 token 缓存。

        条件：缓存存在、creation_date 是今天；缓存可能是 Windows DPAPI 加密格式
        或受限权限的明文 JSON。Windows 上的明文缓存首次读取时迁移为加密格式；
        其他平台继续使用 0o600 明文缓存。解密失败视为无缓存，回退重新登录。
        """
        if not os.path.exists(self._token_cache_path):
            return None
        try:
            with open(self._token_cache_path, "rb") as f:
                raw = f.read()
        except OSError:
            return None

        if secure_token_cache.is_encrypted(raw):
            try:
                payload = secure_token_cache.decrypt(raw)
            except secure_token_cache.DecryptError as exc:
                logger.warning(
                    "[cache] token 缓存解密失败，将重新登录: %s",
                    redact_sensitive_text(exc),
                )
                return None
            try:
                cache = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                logger.warning("[cache] token 缓存内容损坏，将重新登录")
                return None
            legacy = False
        else:
            try:
                cache = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                return None
            # 非 Windows 平台没有 DPAPI，0600 明文就是当前格式；只有 Windows
            # 才把明文视为旧格式并迁移，避免 macOS 每次读取都重复写盘和告警。
            legacy = os.name == "nt"

        if not isinstance(cache, dict):
            logger.warning("[cache] token 缓存格式无效，将重新登录")
            return None

        if cache.get("creation_date") != date.today().isoformat():
            logger.info("[cache] token 缓存非今日，跳过")
            return None
        if (
            not cache.get("access_token")
            or not cache.get("user_id")
            or not cache.get("jwt_token")
        ):
            return None
        if legacy:
            # Windows 会迁移为 DPAPI；其他平台重写为 0o600 明文并记录安全告警。
            self._write_token_cache(cache)
        return cache

    def _save_token_cache(self) -> bool:
        """保存当前 token 信息到缓存文件。"""
        if self._token_info is None:
            return False
        cache = {
            "creation_date": date.today().isoformat(),
            "access_token": self._token_info.access_token,
            "refresh_token": self._token_info.refresh_token,
            "user_id": self._token_info.user_id,
            "user_name": self._token_info.user_name,
            "national_code": self._token_info.national_code,
            "real_name": self._token_info.real_name,
            "jwt_token": self._token_info.jwt_token,
        }
        return self._write_token_cache(cache)

    def _write_token_cache(self, cache: dict) -> bool:
        """把 token 缓存写入磁盘（DPAPI 加密；原子替换，失败保留原缓存）。"""
        os.makedirs(self.state_dir, exist_ok=True)
        tmp_path = self._token_cache_path + ".tmp"
        try:
            payload = json.dumps(cache, ensure_ascii=False).encode("utf-8")
            data = secure_token_cache.protect(payload)
            with open(tmp_path, "wb") as f:
                f.write(data)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError as exc:
                logger.debug(
                    "无法限制 token 缓存文件权限: %s",
                    redact_sensitive_text(exc),
                )
            os.replace(tmp_path, self._token_cache_path)
        except OSError as exc:
            # 写入或替换失败时保留原缓存，不留下会被当成正式缓存读取的半截文件。
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            logger.warning(
                "保存 token 缓存失败，保留原缓存: %s",
                redact_sensitive_text(exc),
            )
            return False
        logger.info("[cache] token 缓存已保存: %s", self._token_cache_path)
        return True

    def _init_client_from_cache(self, cache: dict) -> None:
        """从缓存数据初始化 API 客户端。"""
        self._token_info = TokenInfo(
            access_token=cache["access_token"],
            refresh_token=cache.get("refresh_token", ""),
            user_id=cache["user_id"],
            user_name=cache.get("user_name", ""),
            national_code=cache.get("national_code", ""),
            real_name=cache.get("real_name", False),
            jwt_token=cache.get("jwt_token", ""),
        )
        self._client = HuaweiSignClient(
            access_token=self._token_info.access_token,
            uid=self._token_info.user_id,
            cancel_event=self.cancel_event,
        )
        self._cert_api = CertAPI(self._client)
        self._device_api = DeviceAPI(self._client)
        self._provision_api = ProvisionAPI(self._client)
        self._capability_api = CapabilityAPI(self._client)
        self._token_from_cache = True
        logger.info("[cache] 使用缓存 token")

    def _clear_token_cache(self) -> None:
        """清除 token 缓存文件。"""
        if os.path.exists(self._token_cache_path):
            os.remove(self._token_cache_path)
            logger.info("[cache] token 缓存已清除")

    # ── 签名文件缓存 ─────────────────────────────────────────────

    def _load_cached_metadata(self) -> dict | None:
        """加载当天的签名文件缓存。"""
        if not os.path.exists(self._metadata_path):
            return None
        try:
            with open(self._metadata_path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None

        if not isinstance(meta, dict):
            logger.warning("[cache] 签名文件元数据格式无效，将重新申请")
            return None

        if meta.get("creation_date") != date.today().isoformat():
            logger.info("[cache] 签名文件创建于 %s，非今日", meta.get("creation_date"))
            return None

        if meta.get("bundle_name") != self.bundle_name:
            logger.info("[cache] 签名文件包名不匹配，将重新申请")
            return None

        cached_capability = meta.get("enable_capability")
        cached_request = meta.get("requested_enable_capability", cached_capability)
        if (
            not isinstance(cached_capability, bool)
            or not isinstance(cached_request, bool)
            or (cached_capability and not cached_request)
            or cached_request != self.requested_enable_capability
        ):
            logger.info("[cache] 签名文件能力模式不匹配，将重新申请")
            return None

        cached_udid = str(meta.get("udid", "")).strip()
        if not is_valid_device_udid(cached_udid):
            logger.info("[cache] 签名文件缺少有效设备 UDID，将重新申请")
            return None
        if self._udid and cached_udid.lower() != self._udid.lower():
            logger.info("[cache] 签名文件设备不匹配，将重新申请")
            return None
        for key in ("p12_path", "cer_path", "p7b_path"):
            path = meta.get(key, "")
            if not path or not os.path.isfile(path):
                logger.info("[cache] 签名文件缺失 (%s)", key)
                return None
        if not self._udid:
            self._udid = cached_udid
        self.enable_capability = cached_capability
        return meta

    def _save_metadata(
        self,
        p12_path: str,
        cer_path: str,
        p7b_path: str,
        team_id: str,
        cert_object_id: str,
        udid: str,
    ) -> None:
        """保存签名材料元数据。"""
        meta = {
            "creation_date": date.today().isoformat(),
            "bundle_name": self.bundle_name,
            "p12_path": p12_path,
            "cer_path": cer_path,
            "p7b_path": p7b_path,
            "team_id": team_id,
            "cert_object_id": cert_object_id,
            "udid": udid,
            "key_alias": KEY_ALIAS,
            "requested_enable_capability": self.requested_enable_capability,
            "enable_capability": self.enable_capability,
        }
        with open(self._metadata_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        logger.info("[cache] 签名文件元数据已保存: %s", self._metadata_path)

    # ── 主流程 ──────────────────────────────────────────────────

    def run(self) -> bool:
        """执行流程，并清理由本次任务启动的 HDC server。"""
        try:
            self._check_cancelled()
            self._emit_progress(2, "正在准备")
            result = self._run_pipeline()
            if result:
                final_label = "安装完成" if self.install_after_sign else "签名完成"
                self._emit_progress(100, final_label)
            return result
        finally:
            self._close_installer()
            self._cleanup_temporary_signed_hap()

    def _run_pipeline(self) -> bool:
        """执行签名安装流程，优先使用缓存。

        强制刷新逻辑（全部是本次运行的局部决策，不改写构造参数）：
        - force_refresh_token: 清除 token 缓存，强制重新登录
        - force_refresh_signing: 清除签名文件缓存，强制重新申请
        - 刷新 token 时自动连带刷新签名文件（否则只刷新 token 无意义）

        Returns:
            全部步骤成功返回 True，任一步骤失败返回 False。
        """
        # 兼容旧行为：签名并安装时仍在读取 HAP、登录和申请材料前确认设备可用。
        # 仅签名时可以复用缓存 Profile，或由调用方直接提供设备 UDID。
        device_checked = False
        if self.install_after_sign:
            if not self._run_steps([("检测设备连接", self._step_check_device)]):
                return False
            device_checked = True

        # 已签名包跳过重签名；显式输出仍应兑现，安装时也使用发布后的路径。
        if is_hap_signed(self.hap_path):
            logger.info("[sign] 检测到已签名 HAP，跳过签名流程")
            self._signed_hap_path = self.hap_path
            if self.signed_output_path:
                self._publish_existing_signed_hap()
            steps = []
            if self.install_after_sign:
                steps.append(("安装 hap 到设备", self._step_install))
            return self._run_steps(steps)

        # 仅签名允许完全离线复用缓存，但显式给出 serial 时必须先读取该目标
        # 的 UDID，避免误用另一个设备的 Profile。
        if self.serial and not device_checked:
            if not self._run_steps([("检测设备连接", self._step_check_device)]):
                return False
            device_checked = True

        # 强制刷新 token 时连带刷新签名文件（仅本次运行，不改写实例属性）
        refresh_signing = self.force_refresh_signing
        if self.force_refresh_token:
            self._clear_token_cache()
            logger.info("[cache] 强制刷新 token 缓存")
            refresh_signing = True

        if not refresh_signing:
            signing_cached = self._load_cached_metadata()
        else:
            signing_cached = None
            logger.info("[cache] 强制刷新签名文件缓存")

        if signing_cached:
            logger.info("[cache] 当天签名文件已缓存，跳过登录和申请步骤")
            self._p12_path = signing_cached["p12_path"]
            self._cer_path = signing_cached["cer_path"]
            self._p7b_path = signing_cached["p7b_path"]
            steps = [
                ("签名 hap", self._step_sign_hap),
            ]
            if self.install_after_sign:
                steps.append(("安装 hap 到设备", self._step_install))
        else:
            if (
                not self._udid
                and not device_checked
                and not self._run_steps([("检测设备连接", self._step_check_device)])
            ):
                return False
            # 需要申请签名文件，先确保有 token
            if not self.force_refresh_token:
                token_cached = self._load_token_cache()
            else:
                token_cached = None
            if token_cached:
                logger.info("[cache] 当天 token 已缓存，跳过登录")
                self._init_client_from_cache(token_cached)
            else:
                # 没有缓存 token，需要登录
                try:
                    self._emit_progress(18, "等待华为账号授权")
                    self._step_login()
                    self._emit_progress(30, "正在完成登录")
                    self._step_exchange_token()
                except OperationCancelled:
                    raise
                except Exception as e:
                    self._last_error = f"登录失败: {redact_sensitive_text(e)}"
                    logger.error("x 登录失败: %s", redact_sensitive_text(e))
                    logger.debug(
                        "登录失败调用栈:\n%s",
                        redact_sensitive_text(traceback.format_exc()),
                    )
                    return False

            steps = [
                ("生成密钥对和 CSR", self._step_generate_keypair),
                ("申请证书", self._step_add_certificate),
                ("注册调试设备", self._step_register_device),
                ("创建调试 Profile", self._step_create_provision),
                ("签名 hap", self._step_sign_hap),
            ]
            if self.install_after_sign:
                steps.append(("安装 hap 到设备", self._step_install))
            if self.enable_capability:
                steps.insert(0, ("查询应用信息", self._step_get_app_info))

        return self._run_steps(steps)

    def _run_steps(self, steps: list) -> bool:
        """按顺序执行步骤，处理 token 失效重试。"""
        progress_by_step = {
            "检测设备连接": 7,
            "查询应用信息": 38,
            "生成密钥对和 CSR": 40,
            "申请证书": 52,
            "注册调试设备": 64,
            "创建调试 Profile": 75,
            "签名 hap": 86,
            "安装 hap 到设备": 95,
        }
        for name, step in steps:
            self._check_cancelled()
            self._emit_progress(progress_by_step.get(name, 10), name)
            logger.info("> %s ...", name)
            try:
                step()
                self._check_cancelled()
            except TokenExpiredError as e:
                # 只有 token 真正失效才回退到重新登录
                logger.warning(
                    "x %s failed: token 已失效 (%s)，回退到重新登录",
                    name,
                    redact_sensitive_text(e),
                )
                self._token_from_cache = False
                self._clear_token_cache()
                try:
                    self._emit_progress(18, "登录已失效，等待重新授权")
                    self._step_login()
                    self._emit_progress(30, "正在刷新登录")
                    self._step_exchange_token()
                    step()
                except OperationCancelled:
                    raise
                except Exception as retry_err:
                    self._last_error = f"{name}: {redact_sensitive_text(retry_err)}"
                    logger.error(
                        "x %s failed after retry: %s",
                        name,
                        redact_sensitive_text(retry_err),
                    )
                    logger.debug(
                        "%s 重试失败调用栈:\n%s",
                        name,
                        redact_sensitive_text(traceback.format_exc()),
                    )
                    return False
            except OperationCancelled:
                raise
            except Exception as e:
                self._last_error = f"{name}: {redact_sensitive_text(e)}"
                logger.error("x %s failed: %s", name, redact_sensitive_text(e))
                logger.debug(
                    "%s 失败调用栈:\n%s",
                    name,
                    redact_sensitive_text(traceback.format_exc()),
                )
                return False
            logger.info("+ %s done", name)
        return True

    # ── 步骤实现 ────────────────────────────────────────────────

    def _step_login(self) -> None:
        """Playwright 浏览器登录，用户手动输入账号密码，获取 tempToken。"""
        login = BrowserLogin(
            browser_mode=self.browser_mode,
            cancel_event=self.cancel_event,
        )
        self._temp_token = login.login(self.country)

    def _step_exchange_token(self) -> bool:
        """tempToken -> jwtToken -> accessToken，初始化 API 客户端，保存 token 缓存。"""
        jwt_token = self._token_exchange.exchange_temp_token(
            self._temp_token, self.country
        )
        token_info = self._token_exchange.get_access_token(jwt_token)
        self._token_info = token_info
        logger.info("登录成功")

        self._client = HuaweiSignClient(
            access_token=token_info.access_token,
            uid=token_info.user_id,
            cancel_event=self.cancel_event,
        )
        self._cert_api = CertAPI(self._client)
        self._device_api = DeviceAPI(self._client)
        self._provision_api = ProvisionAPI(self._client)
        self._capability_api = CapabilityAPI(self._client)

        # 保存 token 缓存，供同一天内复用
        return self._save_token_cache()

    def authenticate(self, force_refresh: bool = False) -> dict[str, object]:
        """确保账号认证缓存可用，不执行设备检测、签名或安装。"""
        if force_refresh:
            self._clear_token_cache()
        cache = self._load_token_cache()
        if cache is not None:
            self._init_client_from_cache(cache)
            return {
                "authenticated": True,
                "from_cache": True,
                "creation_date": cache.get("creation_date", ""),
            }

        self._step_login()
        if not self._step_exchange_token():
            raise RuntimeError("认证成功，但 Token 缓存写入失败")
        return {
            "authenticated": True,
            "from_cache": False,
            "creation_date": date.today().isoformat(),
        }

    def auth_status(self) -> dict[str, object]:
        """返回不含凭据内容的本地认证缓存状态。"""
        cache = self._load_token_cache()
        return {
            "authenticated": cache is not None,
            "cached": cache is not None,
            "creation_date": cache.get("creation_date", "") if cache else "",
            "cache_path": os.path.abspath(self._token_cache_path),
            "online_verified": False,
        }

    @property
    def signed_hap_path(self) -> str:
        """返回本次运行选择或生成的签名 HAP 路径。"""
        return self._signed_hap_path

    @property
    def last_error(self) -> str:
        """返回适合 CLI 展示的最近一次脱敏流程错误。"""
        return self._last_error

    def _step_get_app_info(self) -> None:
        """查询应用简要信息，获取 appId 和 projectId。

        Real Provision 路径需要 appId 放入 provision/add 请求体。
        逆向自 AutoSigningHandleService.autoSignWhenEnableCapability。

        如果应用未在 AGC 注册（appInfos 为空），会回退到 Test Provision。
        """
        assert self._capability_api is not None
        team_id = self._with_refresh(self._cert_api.get_team_id)
        self._team_id = team_id
        logger.info("Team ID: %s", team_id)

        self._app_info = self._with_refresh(
            self._capability_api.get_app_brief_info, team_id, self.bundle_name
        )
        if self._app_info is None:
            logger.warning(
                "应用 %s 未在 AGC 注册，回退到 Test Profile（APL=normal）",
                self.bundle_name,
            )
            self.enable_capability = False
            return

        if self._app_info.pure_flag != 1:
            logger.warning(
                "应用 pureFlag=%s 非 HarmonyOS 应用，回退到 Test Profile",
                self._app_info.pure_flag,
            )
            self.enable_capability = False
            return

        logger.info(
            "App ID: %s, Project ID: %s",
            self._app_info.app_id,
            self._app_info.project_id,
        )

    def _step_generate_keypair(self) -> None:
        """keytool 生成 EC 密钥对和 CSR。"""
        self._p12_path = os.path.join(
            self.work_dir, f"auto_debug_{self.bundle_name}.p12"
        )
        self._csr_path = os.path.join(
            self.work_dir, f"auto_debug_{self.bundle_name}.csr"
        )

        keytool = KeytoolUtil(cancel_event=self.cancel_event)
        keytool.generate_keypair(self._p12_path, KEY_ALIAS, self.keystore_password)
        self._csr_content = keytool.generate_csr(
            self._p12_path, KEY_ALIAS, self.keystore_password, self._csr_path
        )

    def _step_add_certificate(self) -> None:
        """上传 CSR 申请证书，查询 certObjectId，下载 .cer 文件。

        流程（逆向自 AutoSigningHandleService.generateCertificate）：
        1. 删除同名旧证书（避免 cert/add 冲突）
        2. POST cert/add → 只检查 code=0
        3. GET cert/list → 按证书名匹配获取 certObjectId
        4. POST reapply.url with sourceUrls=certObjectId → 获取下载 URL
        5. 下载 .cer 文件
        """
        assert self._cert_api is not None
        if not self._team_id:
            self._team_id = self._with_refresh(self._cert_api.get_team_id)
        team_id = self._team_id
        logger.info("Team ID: %s", team_id)

        try:
            self._with_refresh(self._cert_api.sign_agreement)
        except OperationCancelled:
            raise
        except Exception:
            logger.debug("Agreement signing skipped (may already be signed)")

        cert_name = f"auto_debug_{team_id}.cer"

        # 删除同名旧证书（与 DevEco deleteRemoteSignData 一致）
        try:
            old_cert = self._with_refresh(self._cert_api.find_cert, team_id, cert_name)
            old_id = str(old_cert.get("id", ""))
            if old_id:
                self._with_refresh(self._cert_api.delete_certificate, team_id, old_id)
                logger.info("Deleted old certificate (id=%s)", old_id)
        except ValueError:
            logger.debug("No existing certificate to delete")
        except OperationCancelled:
            raise
        except Exception:
            logger.debug("Delete old certificate failed, continuing")

        # 上传 CSR（Real Provision 路径需要 reqSource="IDE"）
        req_source = "IDE" if self.enable_capability else None
        self._with_refresh(
            self._cert_api.add_certificate,
            self._csr_content,
            team_id,
            cert_name,
            req_source,
        )

        # 查询 certObjectId 和 cert id
        cert_info = self._with_refresh(self._cert_api.find_cert, team_id, cert_name)
        cert_object_id = str(cert_info.get("certObjectId", ""))
        cert_id = str(cert_info.get("id", ""))
        self._cert_result = CertResult(cert_object_id=cert_object_id, cert_id=cert_id)
        logger.info("Cert ID: %s, ObjectId: %s", cert_id, cert_object_id)

        self._cer_path = os.path.join(
            self.work_dir, f"auto_debug_{self.bundle_name}.cer"
        )
        self._with_refresh(
            self._cert_api.download_file,
            cert_object_id,
            team_id,
            self._cer_path,
        )

    def _step_register_device(self) -> None:
        """获取设备 UDID 并注册到华为平台，然后查询设备 ID。"""
        assert self._device_api is not None
        if not self._udid:
            self._udid = self._get_installer().get_udid()

        self._with_refresh(
            self._device_api.add_device,
            self._udid,
            self.device_type,
            self._team_id,
        )

        # 查询设备列表获取设备 ID（provision API 需要的是 id 不是 udid）
        self._device_id = self._with_refresh(
            self._device_api.find_device_id, self._team_id, self._udid
        )
        logger.info("设备 ID: %s", self._device_id)

    def _step_create_provision(self) -> None:
        """创建调试 Profile，下载 .p7b 文件，删除远端 Profile，保存缓存元数据。

        provision API 的 certList 和 deviceList 需要的是 id（短数字），
        不是 certObjectId / udid（长字符串）。

        根据 enable_capability 选择：
        - Test Profile（APL=normal）：add.test.provision
        - Real Profile（APL=system_basic）：add.real.provision，额外需要 appId

        权限列表从 hap 的 module.json 提取 requestPermissions，
        通过 aclPermissionList 传递（compileSdk >= 9 时使用 ACL 预授权）。

        下载 .p7b 后删除远端 Profile（与 Java generateTestProfileFile 一致），
        因为 Profile 创建后会留下远端记录，需要清理。
        """
        assert self._provision_api is not None and self._cert_api is not None
        acl_perms = self._extract_permissions()
        if acl_perms:
            logger.info("ACL 权限列表: %s", acl_perms)

        if self.enable_capability and self._app_info is not None:
            logger.info("使用 Real Profile（APL=system_basic）")
            result: ProvisionResult = self._with_refresh(
                self._provision_api.add_real_provision,
                self._team_id,
                self.bundle_name,
                [self._cert_result.cert_id],
                [self._device_id],
                self._app_info.app_id,
                acl_permissions=acl_perms,
            )
        else:
            result = self._with_refresh(
                self._provision_api.add_test_provision,
                self._team_id,
                self.bundle_name,
                [self._cert_result.cert_id],
                [self._device_id],
                acl_permissions=acl_perms,
            )

        self._p7b_path = os.path.join(
            self.work_dir, f"auto_debug_{self.bundle_name}.p7b"
        )
        self._with_refresh(
            self._cert_api.download_file,
            result.provision_file_url,
            self._team_id,
            self._p7b_path,
        )

        # 删除远端 Profile（与 Java generateTestProfileFile 中的 deleteProvision 一致）
        if result.provision_id:
            try:
                self._with_refresh(
                    self._provision_api.delete_provision,
                    self._team_id,
                    result.provision_id,
                )
                logger.info("已删除远端 Profile (id=%s)", result.provision_id)
            except OperationCancelled:
                raise
            except Exception as e:
                logger.debug(
                    "删除远端 Profile 失败（不影响流程）: %s",
                    redact_sensitive_text(e),
                )

        self._save_metadata(
            p12_path=self._p12_path,
            cer_path=self._cer_path,
            p7b_path=self._p7b_path,
            team_id=self._team_id,
            cert_object_id=self._cert_result.cert_object_id,
            udid=self._udid,
        )

    def _step_sign_hap(self) -> None:
        """用 hap-sign-tool 对 hap 包签名。"""
        hap_basename = os.path.splitext(os.path.basename(self.hap_path))[0]
        explicit_output = bool(self.signed_output_path)
        keep_output = self.keep_signed_hap or explicit_output
        if explicit_output:
            final_path = os.path.abspath(self.signed_output_path)
            output_path = self._prepare_staged_output(
                final_path,
                operation="signing",
                refuse_existing=not self.overwrite_output,
            )
        elif keep_output:
            final_path = os.path.join(
                self.signed_output_dir,
                f"{hap_basename}_signed.hap",
            )
            output_path = self._prepare_staged_output(
                final_path,
                operation="signing",
                refuse_existing=False,
            )
        else:
            self._temporary_signed_dir = tempfile.TemporaryDirectory(
                prefix="hapsign-signed-"
            )
            output_path = os.path.join(
                self._temporary_signed_dir.name,
                f"{hap_basename}_signed.hap",
            )

        signer = HapSigner(cancel_event=self.cancel_event)
        try:
            signer.sign_hap(
                self.hap_path,
                self._cer_path,
                self._p7b_path,
                self._p12_path,
                KEY_ALIAS,
                self.keystore_password,
                output_path,
            )
            if keep_output:
                # 签名失败不会破坏上一份同名产物或其他 HAP；显式输出还会在发布
                # 时再次执行不可覆盖门禁，避免并发任务越过签名前的快速检查。
                self._publish_staged_output(
                    output_path,
                    final_path,
                    overwrite=not explicit_output or self.overwrite_output,
                )
                if not explicit_output:
                    self._cleanup_previous_signed_haps(final_path)
                    self._save_signed_hap_manifest(final_path)
            else:
                self._signed_hap_path = output_path
        except BaseException:
            if keep_output:
                self._cleanup_staged_output(output_path)
            raise
        logger.info("签名后 hap: %s", self._signed_hap_path)

    def _publish_existing_signed_hap(self) -> None:
        """跳过重签名时仍按显式输出和覆盖策略原子发布 HAP。"""
        final_path = os.path.abspath(self.signed_output_path)
        temporary_path = self._prepare_staged_output(
            final_path,
            operation="copying",
            refuse_existing=not self.overwrite_output,
        )
        try:
            shutil.copy2(self.hap_path, temporary_path)
            self._publish_staged_output(
                temporary_path,
                final_path,
                overwrite=self.overwrite_output,
            )
        except BaseException:
            self._cleanup_staged_output(temporary_path)
            raise

    @staticmethod
    def _prepare_staged_output(
        final_path: str,
        *,
        operation: str,
        refuse_existing: bool,
    ) -> str:
        """为同目录原子发布准备唯一暂存路径，并执行早期不覆盖检查。"""
        output_directory = os.path.dirname(final_path)
        os.makedirs(output_directory, exist_ok=True)
        if refuse_existing and os.path.exists(final_path):
            raise FileExistsError(
                f"签名输出已存在：{final_path}；如需覆盖请显式启用覆盖"
            )
        return os.path.join(
            output_directory,
            f".hapsign-{uuid.uuid4().hex}.{operation}.tmp.hap",
        )

    def _publish_staged_output(
        self,
        staged_path: str,
        final_path: str,
        *,
        overwrite: bool,
    ) -> None:
        """按覆盖策略发布完整暂存文件，并记录最终产物路径。"""
        if overwrite:
            os.replace(staged_path, final_path)
        else:
            self._publish_without_overwrite(staged_path, final_path)
        self._signed_hap_path = final_path

    @staticmethod
    def _cleanup_staged_output(staged_path: str) -> None:
        try:
            os.remove(staged_path)
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(
                "无法清理签名临时文件 %s：%s",
                staged_path,
                redact_sensitive_text(exc),
            )

    @staticmethod
    def _publish_without_overwrite(source_path: str, final_path: str) -> None:
        """发布完整产物且绝不替换并发创建的目标文件。"""

        def already_exists() -> FileExistsError:
            return FileExistsError(
                f"签名输出已存在：{final_path}；如需覆盖请显式启用覆盖"
            )

        try:
            # 源与目标位于同一目录。硬链接让完整文件原子可见，并由文件系统保证
            # 目标必须不存在；NTFS、Linux 和 macOS 常见文件系统均支持。
            os.link(source_path, final_path)
        except FileExistsError as exc:
            raise already_exists() from exc
        except OSError:
            # FAT/exFAT 等文件系统不支持硬链接。用 O_EXCL 保留“不覆盖”保证；
            # 复制失败时删除不完整目标，调用方仍会清理签名临时文件。
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
            try:
                descriptor = os.open(final_path, flags, 0o600)
            except FileExistsError as exc:
                raise already_exists() from exc
            try:
                with (
                    os.fdopen(descriptor, "wb") as target,
                    open(source_path, "rb") as source,
                ):
                    shutil.copyfileobj(source, target)
                    target.flush()
                    os.fsync(target.fileno())
            except BaseException:
                try:
                    os.remove(final_path)
                except OSError:
                    pass
                raise
        try:
            os.remove(source_path)
        except OSError as exc:
            # 产物已完整发布，残留的唯一 UUID 隐藏临时名不应让签名结果失败。
            logger.warning(
                "签名产物已发布，但无法清理临时文件 %s：%s",
                source_path,
                redact_sensitive_text(exc),
            )

    def _cleanup_previous_signed_haps(self, final_path: str) -> None:
        """只清理 manifest 记录的旧产物，绝不按扩展名删除用户文件。"""
        manifest_path = os.path.join(self.signed_output_dir, SIGNED_HAP_MANIFEST)
        try:
            with open(manifest_path, encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return

        if not isinstance(manifest, dict):
            return
        generated_haps = manifest.get("generated_haps")
        if not isinstance(generated_haps, list):
            return

        final_resolved = os.path.normcase(os.path.realpath(os.path.abspath(final_path)))
        input_resolved = os.path.normcase(
            os.path.realpath(os.path.abspath(self.hap_path))
        )
        for name in generated_haps:
            if (
                not isinstance(name, str)
                or os.path.basename(name) != name
                or os.path.splitext(name)[1].lower() != ".hap"
            ):
                continue
            candidate = os.path.join(self.signed_output_dir, name)
            candidate_resolved = os.path.normcase(
                os.path.realpath(os.path.abspath(candidate))
            )
            if candidate_resolved in {final_resolved, input_resolved}:
                continue
            if not os.path.isfile(candidate):
                continue
            try:
                os.remove(candidate)
            except OSError as exc:
                logger.warning(
                    "无法删除旧的签名 HAP %s：%s",
                    candidate,
                    redact_sensitive_text(exc),
                )

    def _save_signed_hap_manifest(self, final_path: str) -> None:
        """原子保存本次生成的签名 HAP 清单。"""
        manifest_path = os.path.join(self.signed_output_dir, SIGNED_HAP_MANIFEST)
        temporary_path = f"{manifest_path}.tmp"
        manifest = {
            "version": 1,
            "generated_haps": [os.path.basename(final_path)],
        }
        try:
            with open(temporary_path, "w", encoding="utf-8") as manifest_file:
                json.dump(manifest, manifest_file, indent=2, ensure_ascii=False)
            os.replace(temporary_path, manifest_path)
        except OSError as exc:
            try:
                os.remove(temporary_path)
            except OSError:
                pass
            logger.warning(
                "无法保存签名 HAP 清单 %s：%s",
                manifest_path,
                redact_sensitive_text(exc),
            )

    def _step_install(self) -> None:
        """hdc install 安装签名后的 hap 到设备。"""
        self._get_installer().install(self._signed_hap_path)

    def _step_check_device(self) -> None:
        """读取显式目标或 HDC 默认目标的 UDID，确认设备可访问。"""
        self._udid = self._get_installer().get_udid()
        logger.info("已检测到可用设备（UDID 尾号 %s）", self._udid[-6:])

    # ── 工具方法 ────────────────────────────────────────────────

    def _get_installer(self) -> Installer:
        if self._installer is None:
            self._installer = Installer(
                cancel_event=self.cancel_event,
                serial=self.serial,
            )
        return self._installer

    def _cleanup_temporary_signed_hap(self) -> None:
        if self._temporary_signed_dir is None:
            return
        try:
            self._temporary_signed_dir.cleanup()
        finally:
            self._temporary_signed_dir = None

    def _close_installer(self) -> None:
        if self._installer is None:
            return
        self._installer.close()
        self._installer = None

    def _extract_permissions(self) -> list[str]:
        """从 hap 的 module.json 提取 requestPermissions，按 ACL 白名单过滤。

        DevEco 的 PermissionUtil.getReqPermissions 会按 SDK 版本对应的白名单
        过滤权限，只把白名单内的权限放入 aclPermissionList。
        白名单外的权限（如 INTERNET、READ_CALENDAR）是普通权限，
        不需要 ACL 预授权，由系统安装时自动授予。
        """
        try:
            with zipfile.ZipFile(self.hap_path) as z:
                for name in z.namelist():
                    if name.lower() == "module.json":
                        data = json.loads(z.read(name))
                        perms = data.get("module", {}).get("requestPermissions", [])
                        all_names = [p["name"] for p in perms if "name" in p]
                        filtered = [
                            n for n in all_names if n in ACL_PERMISSION_WHITELIST
                        ]
                        skipped = [
                            n for n in all_names if n not in ACL_PERMISSION_WHITELIST
                        ]
                        if skipped:
                            logger.info(
                                "非 ACL 权限（普通权限，由系统授予）: %s", skipped
                            )
                        return filtered
        except OperationCancelled:
            raise
        except Exception as e:
            logger.debug("提取权限列表失败: %s", redact_sensitive_text(e))
        return []

    def _with_refresh(self, func, *args, **kwargs):
        """执行 API 调用，token 失效时自动刷新并重试一次。"""
        self._check_cancelled()
        try:
            result = func(*args, **kwargs)
            self._check_cancelled()
            return result
        except TokenExpiredError:
            logger.warning("Token 失效，尝试刷新...")
            new_token = self._token_exchange.refresh_access_token(
                self._token_info.jwt_token
            )
            self._client.access_token = new_token
            self._token_info.access_token = new_token
            self._save_token_cache()
            self._check_cancelled()
            result = func(*args, **kwargs)
            self._check_cancelled()
            return result
