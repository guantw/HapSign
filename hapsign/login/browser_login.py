"""Playwright 浏览器登录模块。

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

import json
import logging
import os
import threading
import time
import uuid
import webbrowser
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from hapsign.cancellation import OperationCancelled, raise_if_cancelled
from hapsign.config import APP_ID, BASE_URL, LOGIN_AUTH_PATH, LOGIN_SUCCESS_PATH
from hapsign.diagnostics import sensitive_logging_enabled

logger = logging.getLogger(__name__)

# 回调超时（秒）—— 给用户足够时间处理验证码/二次验证
_CALLBACK_TIMEOUT = 300
_CALLBACK_HOST = "127.0.0.1"
_MAX_CALLBACK_BODY_SIZE = 64 * 1024
_CONTROLLED_SYSTEM_CHANNELS = ("msedge", "chrome")


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
            if received_code != expected_code:
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
    ):
        self.browser_mode = (
            browser_mode or os.environ.get("HAPSIGN_BROWSER", "system_controlled")
        ).lower()
        self.cancel_event = cancel_event

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
        server = ThreadingHTTPServer((_CALLBACK_HOST, 0), handler_class)
        server.daemon_threads = True
        server.block_on_close = False
        port = server.server_address[1]
        login_url = (
            f"{BASE_URL}/{LOGIN_AUTH_PATH}?port={port}&appid={APP_ID}&code={csrf_code}"
        )
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        logger.info("[LOGIN] callback port=%d", port)
        logger.info("[LOGIN] callback server listening on %s:%d", _CALLBACK_HOST, port)
        logger.info("[LOGIN] browser mode=%s", self.browser_mode)
        if sensitive_logging_enabled():
            logger.debug("[LOGIN] sensitive login_url=%s", login_url)

        try:
            # ── 5. 启动浏览器，等待回调 ──
            self._browser_login_and_wait(login_url, callback_event)
        finally:
            # ── 6. 关闭 HTTP 服务 ──
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

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
        if self.browser_mode == "system":
            self._system_browser_login_and_wait(login_url, callback_event)
            return
        if self.browser_mode in {"playwright", "system_controlled"}:
            self._playwright_login_and_wait(login_url, callback_event)
            return
        raise RuntimeError(
            f"Unsupported browser mode: {self.browser_mode}. "
            "Use 'system_controlled', 'playwright' or 'system'."
        )

    def _wait_for_callback(self, callback_event: threading.Event) -> None:
        deadline = time.monotonic() + _CALLBACK_TIMEOUT
        while not callback_event.wait(timeout=0.1):
            raise_if_cancelled(self.cancel_event)
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Login timed out: no callback received within "
                    f"{_CALLBACK_TIMEOUT}s. "
                    "Please check your network and complete login in the browser."
                )

    def _system_browser_login_and_wait(
        self,
        login_url: str,
        callback_event: threading.Event,
    ) -> None:
        """使用系统默认浏览器完成登录，不引入 Chromium 运行时。"""
        raise_if_cancelled(self.cancel_event)
        if not webbrowser.open(login_url, new=1, autoraise=True):
            raise RuntimeError(
                "Unable to open the system browser. "
                "Set HAPSIGN_BROWSER=playwright to use the optional backend."
            )
        logger.info("[login] 登录页面已在系统浏览器中打开")
        logger.info(
            "[login] 正在等待登录回调（最多 %s 秒，可处理验证码或二次验证）",
            _CALLBACK_TIMEOUT,
        )
        raise_if_cancelled(self.cancel_event)
        self._wait_for_callback(callback_event)

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
            raise RuntimeError(
                "Playwright not installed. Run:\n"
                "  pip install playwright\n"
                "  playwright install chromium"
            ) from exc

        try:
            with sync_playwright() as pw:
                browser, runtime_name = _launch_controlled_browser(
                    pw,
                    self.browser_mode,
                    allow_fallback=True,
                )
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
                    lambda error: logger.warning("[login] 页面脚本错误：%s", error),
                )

                # 使用 domcontentloaded，避免持续网络请求让 networkidle 超时。
                try:
                    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
                except PwTimeout as exc:
                    raise RuntimeError("Login page load timed out") from exc

                logger.info("[login] 登录页面已打开，请在浏览器中完成登录")
                logger.info(
                    "[login] 正在等待登录回调（最多 %s 秒，可处理验证码或二次验证）",
                    _CALLBACK_TIMEOUT,
                )

                self._wait_for_callback(callback_event)
                browser.close()

        except (RuntimeError, OperationCancelled):
            raise
        except Exception as e:
            raise RuntimeError(f"Browser operation failed: {e}") from e


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
