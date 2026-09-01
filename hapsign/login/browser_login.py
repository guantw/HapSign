"""跨平台浏览器登录与 loopback 回调模块。

使用 Playwright 打开华为 OAuth 登录页，用户在浏览器中手动登录，
通过本地 HTTP 服务拦截回调拿到 tempToken。

流程（逆向自 DevEco Studio HiAiLoginService）：
  1. 生成 CSRF code (UUID 去横线)，在 loopback 上绑定临时端口
  2. 启动本地 HTTP 服务等待授权回调
  3. 系统浏览器打开登录页 ?port={port}&appid=1007&code={uuid}
  4. 用户在浏览器中手动输入账号密码并登录
  5. 华为 OAuth 服务端通过浏览器回调本地 HTTP 服务，携带 tempToken, siteId, code
  6. 校验 code（CSRF），返回 tempToken
"""

import base64
import hmac
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlparse

from hapsign.cancellation import OperationCancelled, raise_if_cancelled
from hapsign.config import APP_ID, BASE_URL, LOGIN_AUTH_PATH, LOGIN_SUCCESS_PATH
from hapsign.diagnostics import redact_sensitive_text, sensitive_logging_enabled

logger = logging.getLogger(__name__)

# 回调超时（秒）—— 外部浏览器交接需要给用户留出建立隧道和处理二次验证的时间。
DEFAULT_AUTH_TIMEOUT = 600
BROWSER_MODES = ("auto", "external", "system", "system_controlled", "playwright")
_CALLBACK_HOST = "127.0.0.1"
_MAX_CALLBACK_BODY_SIZE = 64 * 1024
_CONTROLLED_SYSTEM_CHANNELS = ("msedge", "chrome")


class AuthRequiredEvent(TypedDict):
    """首次认证需要用户接管浏览器时发布的稳定事件结构。"""

    event: Literal["auth_required"]
    method: Literal["manual_loopback", "ssh_loopback", "loopback_forwarding"]
    reason: str
    verification_uri: str
    callback_host: str
    callback_port: int
    expires_in: int


AuthEventCallback = Callable[[AuthRequiredEvent], None]


class BrowserUnavailableError(RuntimeError):
    """当前会话无法启动可见浏览器，但仍可交接到外部浏览器。"""


class _CallbackHTTPServer(ThreadingHTTPServer):
    """独占 loopback 端口，避免 Windows 上多个登录会话复用同一端口。"""

    # POSIX 的 SO_REUSEADDR 允许固定端口跨 TIME_WAIT 快速重试，但不会允许两个
    # 活跃 TCP listener 绑定同一地址；Windows 则配合 SO_EXCLUSIVEADDRUSE 禁用它。
    allow_reuse_address = os.name != "nt"

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_EXCLUSIVEADDRUSE,
                1,
            )
        super().server_bind()


def _browser_environment() -> str:
    """识别影响可见浏览器启动方式的运行环境。"""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return "ssh"
    if os.environ.get("CI"):
        return "headless"
    if not sys.platform.startswith("linux"):
        return "desktop"
    release = platform.release().lower()
    if (
        os.environ.get("WSL_INTEROP")
        or os.environ.get("WSL_DISTRO_NAME")
        or "microsoft" in release
    ):
        return "wsl"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return "desktop"
    return "headless"


def _parse_multipart(body: bytes, content_type: str) -> dict[str, str]:
    """解析浏览器回调的 multipart 表单，兼容 DevEco 的 Netty 解码行为。"""
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: "
        + content_type.encode("ascii", errors="ignore")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        return {}
    params: dict[str, str] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        value = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        params[name] = value.decode(charset, errors="replace")
    return params


def _flatten_params(params: dict) -> dict[str, str]:
    return {
        str(key): str(value[-1] if isinstance(value, list) else value)
        for key, value in params.items()
    }


def _decode_callback_params(body: bytes, content_type: str) -> dict[str, str]:
    """按 Content-Type 解码，并对浏览器省略/误报类型的情况进行安全回退。"""
    media_type = content_type.lower()
    params: dict = {}

    if "application/json" in media_type:
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            decoded = {}
        if isinstance(decoded, dict):
            nested = decoded.get("data")
            params = nested if isinstance(nested, dict) else decoded
    elif "multipart/form-data" in media_type:
        params = _parse_multipart(body, content_type)
    else:
        try:
            params = parse_qs(body.decode("utf-8"))
        except UnicodeDecodeError:
            params = {}

    # 某些授权页会漏掉 multipart Content-Type；从首行恢复 boundary 后重试。
    if not {"tempToken", "code"}.intersection(params) and body.startswith(b"--"):
        first_line = body.splitlines()[0].strip()
        if first_line.startswith(b"--") and len(first_line) > 2:
            boundary = first_line[2:].decode("ascii", errors="ignore")
            params = _parse_multipart(
                body,
                f'multipart/form-data; boundary="{boundary}"',
            )

    return _flatten_params(params)


def _make_callback_handler(
    expected_code: str, callback_data: dict, callback_event: threading.Event
):
    """创建回调 HTTP 请求处理器类（闭包注入共享状态）。

    华为 OAuth 服务端在用户登录成功后，通过浏览器向本地回调服务器发送
    tempToken、siteId 和 code。兼容 DevEco 使用的 multipart 表单及其他版本。
    """

    class _CallbackHandler(BaseHTTPRequestHandler):
        def _send_headers(self, status: int, content_type: str = "text/plain") -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            origin = self.headers.get("Origin", "")
            self.send_header("Access-Control-Allow-Origin", origin or "*")
            if origin:
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            requested_headers = self.headers.get(
                "Access-Control-Request-Headers", "Content-Type"
            )
            self.send_header("Access-Control-Allow-Headers", requested_headers)
            # Chromium 对 HTTPS 页面访问 loopback 可能发起 Private Network
            # Access 预检；允许后才会真正发送带 token 的 POST。
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _send_text(
            self, status: int, message: str, content_type: str = "text/plain"
        ) -> None:
            self._send_headers(status, content_type)
            try:
                self.wfile.write(message.encode("utf-8"))
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("[callback] client closed before reading response")

        def _send_success_redirect(self) -> None:
            self.send_response(302)
            self.send_header("Location", f"{BASE_URL}/{LOGIN_SUCCESS_PATH}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _process_params(self, params: dict) -> None:
            """处理回调参数，校验 CSRF code 并保存 tempToken。"""
            # 回调中含有 tempToken。日志只记录字段名，避免用户分享日志时泄漏凭据。
            logger.debug("[callback] fields received: %s", sorted(params))
            if sensitive_logging_enabled():
                logger.debug("[callback] sensitive params=%r", params)

            received_code = params.get("code", "")
            if not hmac.compare_digest(received_code, expected_code):
                logger.warning("[callback] CSRF code mismatch")
                self._send_text(400, "invalid csrf code")
                return

            temp_token = params.get("tempToken", "")
            if not temp_token:
                denied = params.get("quit", "")
                callback_data["error"] = (
                    "用户取消了华为账号授权"
                    if denied in {"quit", "access_denied"}
                    else "登录回调缺少 tempToken"
                )
                callback_event.set()
                logger.warning("[callback] authorization did not return tempToken")
                self._send_text(400, callback_data["error"], "text/html")
                return

            callback_data["tempToken"] = temp_token
            callback_data["siteId"] = params.get("siteId", "")
            callback_data["code"] = params.get("code", "")
            callback_event.set()
            logger.info("[callback] 授权回调校验成功")
            self._send_success_redirect()

        def do_POST(self):
            try:
                content_length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                self._send_text(400, "invalid content length")
                return
            if content_length < 0:
                self._send_text(400, "invalid content length")
                return
            if content_length > _MAX_CALLBACK_BODY_SIZE:
                self._send_text(413, "request body too large")
                return
            body = self.rfile.read(content_length)
            parsed_path = urlparse(self.path)
            content_type = self.headers.get("Content-Type", "")
            params = _decode_callback_params(body, content_type)
            query_params = parse_qs(urlparse(self.path).query)
            for key, value in _flatten_params(query_params).items():
                params.setdefault(key, value)
            logger.info(
                "[callback] 收到 POST：path=%s, type=%s, bytes=%d, fields=%s",
                parsed_path.path,
                content_type.split(";", 1)[0] or "(missing)",
                len(body),
                sorted(params),
            )
            if sensitive_logging_enabled():
                logger.debug(
                    "[callback] sensitive headers=%r body=%r",
                    dict(self.headers.items()),
                    body.decode("utf-8", errors="replace"),
                )
            self._process_params(params)

        def do_GET(self):
            parsed = urlparse(self.path)
            params = _flatten_params(parse_qs(parsed.query))
            logger.info(
                "[callback] 收到 GET：path=%s, fields=%s",
                parsed.path,
                sorted(params),
            )
            if sensitive_logging_enabled():
                logger.debug(
                    "[callback] sensitive headers=%r query=%r",
                    dict(self.headers.items()),
                    params,
                )
            # DevEco/Huawei 的不同版本可能回调 /、/callback 或其他本地路径。
            # 只要携带了授权字段，就按回调处理，避免授权成功后永远等待。
            if "tempToken" not in params and "code" not in params:
                self._send_text(200, "HapSign callback server is ready")
                return
            self._process_params(params)

        def do_OPTIONS(self):
            logger.info(
                "[callback] 收到浏览器预检：origin=%s, private-network=%s",
                self.headers.get("Origin", "(missing)"),
                self.headers.get("Access-Control-Request-Private-Network", "false"),
            )
            self._send_headers(204)

        def log_message(self, fmt, *args):
            # BaseHTTPRequestHandler 的默认请求行可能包含 tempToken，不记录。
            logger.debug("[callback] loopback request handled")

    return _CallbackHandler


class BrowserLogin:
    """系统浏览器或 Playwright 浏览器登录。

    使用示例::

        login = BrowserLogin()
        temp_token = login.login("CN")
    """

    def __init__(
        self,
        browser_mode: str | None = None,
        cancel_event: threading.Event | None = None,
        callback_port: int = 0,
        callback_timeout: int = DEFAULT_AUTH_TIMEOUT,
        event_callback: AuthEventCallback | None = None,
    ):
        self.browser_mode = (
            browser_mode or os.environ.get("HAPSIGN_BROWSER", "system_controlled")
        ).lower()
        self.cancel_event = cancel_event
        if not 0 <= callback_port <= 65535:
            raise ValueError("callback_port must be between 0 and 65535")
        if callback_timeout <= 0:
            raise ValueError("callback_timeout must be greater than zero")
        self.callback_port = callback_port
        self.callback_timeout = callback_timeout
        self.event_callback = event_callback
        self._active_callback_port = 0

    def login(self, country: str = "CN") -> str:
        """打开华为登录页，用户手动登录，拦截回调拿 tempToken。

        Args:
            country: 国家码，默认 ``"CN"``，仅用于后续 token 交换

        Returns:
            tempToken 字符串

        Raises:
            RuntimeError: 任意步骤失败时抛出
        """
        # ── 1. 生成 CSRF code（UUID 去横线，与原始插件一致） ──
        csrf_code = uuid.uuid4().hex
        callback_data: dict = {}
        callback_event = threading.Event()
        handler_class = _make_callback_handler(csrf_code, callback_data, callback_event)
        # 直接让 HTTPServer 绑定临时端口，消除“先探测、后绑定”的端口竞争窗口。
        try:
            server = _CallbackHTTPServer(
                (_CALLBACK_HOST, self.callback_port),
                handler_class,
            )
        except OSError as exc:
            requested = self.callback_port or "automatic"
            raise RuntimeError(
                f"Unable to bind callback port {requested} on {_CALLBACK_HOST}: {exc}"
            ) from exc
        server.daemon_threads = True
        server.block_on_close = False
        port = server.server_address[1]
        self._active_callback_port = port
        login_url = (
            f"{BASE_URL}/{LOGIN_AUTH_PATH}?port={port}&appid={APP_ID}&code={csrf_code}"
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        try:
            server_thread.start()
            logger.info("[LOGIN] callback port=%d", port)
            logger.info(
                "[LOGIN] callback server listening on %s:%d",
                _CALLBACK_HOST,
                port,
            )
            logger.info("[LOGIN] browser mode=%s", self.browser_mode)
            if sensitive_logging_enabled():
                logger.debug("[LOGIN] sensitive login_url=%s", login_url)

            # ── 5. 启动浏览器，等待回调 ──
            try:
                self._browser_login_and_wait(login_url, callback_event)
            except OperationCancelled:
                raise
            except Exception:
                if not callback_event.is_set():
                    raise
                # 授权页回调成功后通常立即跳转/关闭，浏览器驱动可能同时报告
                # navigation aborted、target closed 等错误；回调结果才是权威状态。
                logger.info("[login] 已收到授权回调，忽略随后发生的浏览器关闭异常")
        finally:
            # ── 6. 关闭 HTTP 服务 ──
            if server_thread.is_alive():
                server.shutdown()
            server.server_close()
            if server_thread.is_alive():
                server_thread.join(timeout=2)
            self._active_callback_port = 0

        # ── 7. 校验并返回 tempToken ──
        temp_token = callback_data.get("tempToken", "")
        if not temp_token:
            callback_error = callback_data.get("error")
            if callback_error:
                raise RuntimeError(callback_error)
            raise RuntimeError(
                "Callback did not contain tempToken. Login may have been cancelled."
            )

        return temp_token

    def _browser_login_and_wait(
        self,
        login_url: str,
        callback_event: threading.Event,
    ) -> None:
        """按配置启动系统浏览器或 Playwright。"""
        if self.browser_mode == "auto":
            environment = _browser_environment()
            if environment in {"ssh", "headless"}:
                self._external_browser_login_and_wait(
                    login_url,
                    callback_event,
                    reason=environment,
                )
                return
            if environment == "wsl":
                try:
                    self._wsl_host_browser_login_and_wait(login_url, callback_event)
                    return
                except BrowserUnavailableError as exc:
                    if callback_event.is_set():
                        return
                    logger.warning(
                        "[login] WSL 宿主浏览器不可用：%s",
                        redact_sensitive_text(exc),
                    )
                    self._external_browser_login_and_wait(
                        login_url,
                        callback_event,
                        reason="wsl_browser_unavailable",
                    )
                    return
            try:
                self._playwright_login_and_wait(login_url, callback_event)
            except BrowserUnavailableError as exc:
                if callback_event.is_set():
                    return
                logger.warning(
                    "[login] 本地受控浏览器不可用：%s",
                    redact_sensitive_text(exc),
                )
                try:
                    self._system_browser_login_and_wait(login_url, callback_event)
                except BrowserUnavailableError as system_exc:
                    if callback_event.is_set():
                        return
                    logger.warning(
                        "[login] 系统默认浏览器不可用：%s",
                        redact_sensitive_text(system_exc),
                    )
                    self._external_browser_login_and_wait(
                        login_url,
                        callback_event,
                        reason="browser_unavailable",
                    )
            return
        if self.browser_mode == "external":
            self._external_browser_login_and_wait(
                login_url,
                callback_event,
                reason=_browser_environment(),
            )
            return
        if self.browser_mode == "system":
            self._system_browser_login_and_wait(login_url, callback_event)
            return
        if self.browser_mode in {"playwright", "system_controlled"}:
            self._playwright_login_and_wait(login_url, callback_event)
            return
        raise RuntimeError(
            f"Unsupported browser mode: {self.browser_mode}. "
            "Use 'auto', 'external', 'system_controlled', 'playwright' or 'system'."
        )

    def _emit_event(self, event: AuthRequiredEvent) -> None:
        if self.event_callback is not None:
            self.event_callback(event)
            return
        if event.get("event") == "auth_required":
            print(
                "External browser login required. Open this one-time URL:\n"
                f"{event['verification_uri']}",
                file=sys.stderr,
                flush=True,
            )

    def _external_browser_login_and_wait(
        self,
        login_url: str,
        callback_event: threading.Event,
        *,
        reason: str,
    ) -> None:
        """不启动浏览器，发布一次性交接信息并等待 loopback 回调。"""
        raise_if_cancelled(self.cancel_event)
        environment = _browser_environment()
        if environment == "ssh":
            method = "ssh_loopback"
        elif environment == "headless":
            method = "loopback_forwarding"
        else:
            method = "manual_loopback"
        self._emit_event(
            {
                "event": "auth_required",
                "method": method,
                "reason": reason,
                "verification_uri": login_url,
                "callback_host": _CALLBACK_HOST,
                "callback_port": self._active_callback_port,
                "expires_in": self.callback_timeout,
            }
        )
        logger.info(
            "[login] 已等待外部浏览器授权：method=%s callback=%s:%d",
            method,
            _CALLBACK_HOST,
            self._active_callback_port,
        )
        self._wait_for_callback(callback_event)

    def _wait_for_callback(self, callback_event: threading.Event) -> None:
        deadline = time.monotonic() + self.callback_timeout
        while not callback_event.wait(timeout=0.1):
            raise_if_cancelled(self.cancel_event)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Login timed out: no callback received within "
                    f"{self.callback_timeout}s. "
                    "Please check your network and complete login in the browser."
                )

    def _system_browser_login_and_wait(
        self,
        login_url: str,
        callback_event: threading.Event,
    ) -> None:
        """使用系统默认浏览器完成登录，不引入 Chromium 运行时。"""
        raise_if_cancelled(self.cancel_event)
        try:
            opened = webbrowser.open(login_url, new=1, autoraise=True)
        except (OSError, webbrowser.Error) as exc:
            raise BrowserUnavailableError(
                f"Unable to open the system browser: {redact_sensitive_text(exc)}"
            ) from exc
        if not opened:
            raise BrowserUnavailableError(
                "Unable to open the system browser. "
                "Set HAPSIGN_BROWSER=playwright to use the optional backend."
            )
        logger.info("[login] 登录页面已在系统浏览器中打开")
        logger.info(
            "[login] 正在等待登录回调（最多 %s 秒，可处理验证码或二次验证）",
            self.callback_timeout,
        )
        raise_if_cancelled(self.cancel_event)
        self._wait_for_callback(callback_event)

    def _wsl_host_browser_login_and_wait(
        self,
        login_url: str,
        callback_event: threading.Event,
    ) -> None:
        """从 WSL 调用 Windows 宿主浏览器，并在 WSL loopback 等待回调。"""
        raise_if_cancelled(self.cancel_event)
        commands: list[tuple[str, list[str]]] = []
        wslview = shutil.which("wslview")
        if wslview:
            commands.append(("wslview", [wslview, login_url]))
        powershell = shutil.which("powershell.exe")
        if powershell:
            quoted_url = login_url.replace("'", "''")
            encoded_command = base64.b64encode(
                f"Start-Process -FilePath '{quoted_url}'".encode("utf-16le")
            ).decode("ascii")
            commands.append(
                (
                    "powershell.exe",
                    [
                        powershell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-EncodedCommand",
                        encoded_command,
                    ],
                )
            )
        failures: list[str] = []
        for runtime_name, command in commands:
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                # TimeoutExpired 等异常会把完整 argv 放进字符串；wslview argv
                # 含一次性 URL，PowerShell argv 含其可逆编码，因此只记录类型。
                failures.append(f"{runtime_name}: {type(exc).__name__}")
                continue
            if result.returncode == 0:
                logger.info("[login] 登录页面已通过 %s 在 Windows 中打开", runtime_name)
                self._wait_for_callback(callback_event)
                return
            failures.append(f"{runtime_name}: exit {result.returncode}")
        detail = " | ".join(failures) if failures else "no WSL browser bridge found"
        raise BrowserUnavailableError(detail)

    def _playwright_login_and_wait(
        self,
        login_url: str,
        callback_event: threading.Event,
    ) -> None:
        """启动 Playwright 浏览器，导航到登录页，等待回调。

        用户在浏览器中手动完成登录（输入账号密码、处理验证码/二次验证）。

        Args:
            login_url: 完整登录 URL
            callback_event: 收到回调后 set，本方法等待其被 set 后退出

        Raises:
            RuntimeError: Playwright 未安装、浏览器启动失败或回调超时
        """
        # 打包时浏览器放在 playwright 包内，源码运行也遵循同一路径。
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
        try:
            from playwright.sync_api import TimeoutError as PwTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "Playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            ) from exc

        page_ready = False
        try:
            with sync_playwright() as pw:
                preferred_mode = (
                    "system_controlled"
                    if self.browser_mode == "auto"
                    else self.browser_mode
                )
                try:
                    browser, runtime_name = _launch_controlled_browser(
                        pw,
                        preferred_mode,
                        allow_fallback=True,
                    )
                except RuntimeError as exc:
                    raise BrowserUnavailableError(str(exc)) from exc
                logger.info("[login] 受控浏览器已启动：%s", runtime_name)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    locale="zh-CN",
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                try:
                    context.grant_permissions(
                        ["local-network-access"],
                        origin=BASE_URL,
                    )
                    logger.info("[login] 已授予登录页访问本地回调服务的权限")
                except Exception as exc:
                    # 旧版 Chromium 不认识该权限；回调服务器仍提供 PNA 预检响应。
                    logger.debug("[login] 无法预授予本地网络权限：%s", exc)
                page = context.new_page()

                def log_request_failure(request) -> None:
                    parsed = urlparse(request.url)
                    safe_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    detail = request.url if sensitive_logging_enabled() else safe_url
                    logger.warning(
                        "[login] 浏览器请求失败：%s %s (%s)",
                        request.method,
                        detail,
                        request.failure,
                    )

                page.on("requestfailed", log_request_failure)
                page.on(
                    "pageerror",
                    lambda error: logger.warning(
                        "[login] 页面脚本错误：%s",
                        redact_sensitive_text(error),
                    ),
                )

                # 使用 domcontentloaded，避免持续网络请求让 networkidle 超时。
                try:
                    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                except PwTimeout as exc:
                    raise BrowserUnavailableError("Login page load timed out") from exc

                page_ready = True
                logger.info("[login] 登录页面已打开，请在浏览器中完成登录")
                logger.info(
                    "[login] 正在等待登录回调（最多 %s 秒，可处理验证码或二次验证）",
                    self.callback_timeout,
                )

                self._wait_for_callback(callback_event)
                browser.close()

        except (BrowserUnavailableError, OperationCancelled):
            raise
        except Exception as exc:
            if not page_ready:
                raise BrowserUnavailableError(
                    f"Controlled browser startup failed: {exc}"
                ) from exc
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Browser operation failed: {exc}") from exc


def _launch_controlled_browser(
    playwright,
    preferred_mode: str,
    *,
    allow_fallback: bool,
    smoke_test: bool = False,
):
    """启动受控浏览器；系统 Edge/Chrome 与内置 Chromium 可互相回退。"""
    system_candidates = [
        (channel, {"channel": channel}) for channel in _CONTROLLED_SYSTEM_CHANNELS
    ]
    bundled_candidate = [("bundled-chromium", {})]
    if preferred_mode == "system_controlled":
        candidates = system_candidates
        if allow_fallback:
            candidates += bundled_candidate
    elif preferred_mode == "playwright":
        candidates = bundled_candidate
        if allow_fallback:
            candidates += system_candidates
    else:
        raise RuntimeError(f"Unsupported controlled browser mode: {preferred_mode}")

    failures: list[str] = []
    for runtime_name, launch_options in candidates:
        if runtime_name == "bundled-chromium":
            executable = Path(playwright.chromium.executable_path)
            if not executable.is_file():
                failures.append("bundled-chromium: runtime not bundled")
                continue
        args = ["--disable-blink-features=AutomationControlled"]
        if smoke_test:
            args.append("--headless=new")
        try:
            browser = playwright.chromium.launch(
                headless=False,
                args=args,
                **launch_options,
            )
            if runtime_name != (
                "bundled-chromium"
                if preferred_mode == "playwright"
                else _CONTROLLED_SYSTEM_CHANNELS[0]
            ):
                logger.warning(
                    "[login] 首选浏览器不可用，已回退到 %s",
                    runtime_name,
                )
            return browser, runtime_name
        except Exception as exc:
            failures.append(f"{runtime_name}: {exc}")

    raise RuntimeError(
        "无法启动受控浏览器。请安装 Microsoft Edge/Google Chrome，"
        "或使用包含内置 Chromium 的兼容包。详情：" + " | ".join(failures)
    )


def playwright_browser_smoke_test(browser_mode: str = "playwright") -> None:
    """严格启动指定受控浏览器，用于验证便携包运行时。"""
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright 未安装") from exc

    with sync_playwright() as pw:
        browser, _ = _launch_controlled_browser(
            pw,
            browser_mode,
            allow_fallback=False,
            smoke_test=True,
        )
        browser.close()
