"""本地登录回调的安全回归测试。"""

import http.client
import logging
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlparse

from hapsign.cancellation import OperationCancelled
from hapsign.diagnostics import set_sensitive_logging
from hapsign.login import browser_login


def test_callback_accepts_token_without_logging_it(caplog) -> None:
    set_sensitive_logging(False)
    callback_data = {}
    callback_event = threading.Event()
    handler = browser_login._make_callback_handler(
        "expected-code", callback_data, callback_event
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    secret = "sensitive-temp-token"

    try:
        caplog.set_level(logging.DEBUG)
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = urlencode({"tempToken": secret, "code": "expected-code"})
        connection.request(
            "POST",
            "/callback",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 302
        assert callback_event.wait(timeout=2)
        assert callback_data["tempToken"] == secret
        assert secret not in caplog.text
    finally:
        server.shutdown()
        server.server_close()


def test_callback_host_is_loopback() -> None:
    assert browser_login._CALLBACK_HOST == "127.0.0.1"


def test_callback_server_uses_platform_appropriate_reuse_policy() -> None:
    assert browser_login._CallbackHTTPServer.allow_reuse_address is (
        browser_login.os.name != "nt"
    )


def test_callback_accepts_get_on_root_path() -> None:
    callback_data = {}
    callback_event = threading.Event()
    handler = browser_login._make_callback_handler(
        "expected-code", callback_data, callback_event
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        query = urlencode(
            {"tempToken": "root-token", "siteId": "CN", "code": "expected-code"}
        )
        connection.request("GET", f"/?{query}")
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 302
        assert response.getheader("Location").endswith(
            "/console/DevEcoIDE/loginSuccess"
        )
        assert callback_event.wait(timeout=2)
        assert callback_data["tempToken"] == "root-token"
    finally:
        server.shutdown()
        server.server_close()


def test_callback_accepts_multipart_form() -> None:
    callback_data = {}
    callback_event = threading.Event()
    handler = browser_login._make_callback_handler(
        "expected-code", callback_data, callback_event
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    boundary = "----HapSignBoundary"
    fields = {
        "tempToken": "multipart-token",
        "siteId": "1",
        "code": "expected-code",
    }
    body = "".join(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f"{value}\r\n"
        for name, value in fields.items()
    )
    body += f"--{boundary}--\r\n"

    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "POST",
            "/callback",
            body.encode("utf-8"),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 302
        assert callback_event.wait(timeout=2)
        assert callback_data["tempToken"] == "multipart-token"
        assert callback_data["siteId"] == "1"
    finally:
        server.shutdown()
        server.server_close()


def test_callback_infers_multipart_when_content_type_is_missing() -> None:
    boundary = "----MissingHeaderBoundary"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="tempToken"\r\n\r\n'
        "inferred-token\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="code"\r\n\r\n'
        "expected-code\r\n"
        f"--{boundary}--\r\n"
    ).encode()

    assert browser_login._decode_callback_params(body, "") == {
        "tempToken": "inferred-token",
        "code": "expected-code",
    }


def test_callback_json_accepts_nested_data() -> None:
    body = b'{"data":{"tempToken":"json-token","code":"expected-code"}}'

    assert browser_login._decode_callback_params(body, "application/json") == {
        "tempToken": "json-token",
        "code": "expected-code",
    }


def test_denied_authorization_finishes_callback_wait() -> None:
    callback_data = {}
    callback_event = threading.Event()
    handler = browser_login._make_callback_handler(
        "expected-code", callback_data, callback_event
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = urlencode({"quit": "access_denied", "code": "expected-code"})
        connection.request(
            "POST",
            "/callback",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 400
        assert callback_event.wait(timeout=2)
        assert "取消" in callback_data["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_post_body_takes_precedence_over_query_params() -> None:
    callback_data = {}
    callback_event = threading.Event()
    handler = browser_login._make_callback_handler(
        "expected-code", callback_data, callback_event
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = urlencode({"tempToken": "body-token", "code": "expected-code"})
        connection.request(
            "POST",
            "/callback?code=wrong-query-code",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 302
        assert callback_event.wait(timeout=2)
        assert callback_data["tempToken"] == "body-token"
    finally:
        server.shutdown()
        server.server_close()


def test_private_network_preflight_is_allowed() -> None:
    handler = browser_login._make_callback_handler(
        "expected-code", {}, threading.Event()
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "OPTIONS",
            "/callback",
            headers={
                "Origin": "https://devecostudio.huawei.com",
                "Access-Control-Request-Headers": "content-type, x-requested-with",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 204
        assert (
            response.getheader("Access-Control-Allow-Origin")
            == "https://devecostudio.huawei.com"
        )
        assert response.getheader("Access-Control-Allow-Private-Network") == "true"
        assert "x-requested-with" in response.getheader("Access-Control-Allow-Headers")
    finally:
        server.shutdown()
        server.server_close()


def test_system_browser_waits_for_callback(monkeypatch) -> None:
    opened = Mock(return_value=True)
    monkeypatch.setattr(browser_login.webbrowser, "open", opened)
    callback_event = threading.Event()
    callback_event.set()

    login = browser_login.BrowserLogin(browser_mode="system")
    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    opened.assert_called_once_with(
        "https://example.invalid/login",
        new=1,
        autoraise=True,
    )


def test_default_browser_mode_is_controlled_system_browser(monkeypatch) -> None:
    monkeypatch.delenv("HAPSIGN_BROWSER", raising=False)
    assert browser_login.BrowserLogin().browser_mode == "system_controlled"


def test_browser_environment_prefers_ssh_over_forwarded_display(monkeypatch) -> None:
    monkeypatch.setattr(browser_login.sys, "platform", "linux")
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 12345 192.0.2.2 22")
    monkeypatch.setenv("DISPLAY", "localhost:10.0")

    assert browser_login._browser_environment() == "ssh"


def test_browser_environment_detects_ssh_on_non_linux(monkeypatch) -> None:
    monkeypatch.setattr(browser_login.sys, "platform", "darwin")
    monkeypatch.setenv("SSH_TTY", "/dev/ttys001")

    assert browser_login._browser_environment() == "ssh"


def test_browser_environment_detects_ci_before_desktop(monkeypatch) -> None:
    monkeypatch.setattr(browser_login.sys, "platform", "win32")
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("SSH_TTY", raising=False)
    monkeypatch.setenv("CI", "true")

    assert browser_login._browser_environment() == "headless"


def test_browser_environment_detects_headless_linux(monkeypatch) -> None:
    monkeypatch.setattr(browser_login.sys, "platform", "linux")
    monkeypatch.setattr(browser_login.platform, "release", lambda: "6.8.0-generic")
    for name in (
        "SSH_CONNECTION",
        "SSH_TTY",
        "WSL_INTEROP",
        "WSL_DISTRO_NAME",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "CI",
    ):
        monkeypatch.delenv(name, raising=False)

    assert browser_login._browser_environment() == "headless"


def test_auto_headless_emits_external_handoff_without_launching_browser(
    monkeypatch,
) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "headless")
    events = []
    callback_event = threading.Event()
    callback_event.set()
    login = browser_login.BrowserLogin(
        browser_mode="auto",
        event_callback=events.append,
    )
    login._active_callback_port = 43123
    monkeypatch.setattr(
        login,
        "_playwright_login_and_wait",
        Mock(side_effect=AssertionError("browser must not be launched")),
    )

    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    assert events == [
        {
            "event": "auth_required",
            "method": "loopback_forwarding",
            "reason": "headless",
            "verification_uri": "https://example.invalid/login",
            "callback_host": "127.0.0.1",
            "callback_port": 43123,
            "expires_in": browser_login.DEFAULT_AUTH_TIMEOUT,
        }
    ]


def test_auto_desktop_falls_back_to_system_browser(monkeypatch) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "desktop")
    callback_event = threading.Event()
    login = browser_login.BrowserLogin(browser_mode="auto")
    controlled = Mock(side_effect=browser_login.BrowserUnavailableError("missing"))
    system = Mock()
    monkeypatch.setattr(login, "_playwright_login_and_wait", controlled)
    monkeypatch.setattr(login, "_system_browser_login_and_wait", system)

    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    controlled.assert_called_once()
    system.assert_called_once_with("https://example.invalid/login", callback_event)


def test_auto_browser_fallback_redacts_login_state(monkeypatch, caplog) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "desktop")
    callback_event = threading.Event()
    login = browser_login.BrowserLogin(browser_mode="auto")
    secret = "sensitive-csrf-value"
    monkeypatch.setattr(
        login,
        "_playwright_login_and_wait",
        Mock(
            side_effect=browser_login.BrowserUnavailableError(
                f"failed at https://example.invalid/login?code={secret}"
            )
        ),
    )
    system = Mock()
    monkeypatch.setattr(login, "_system_browser_login_and_wait", system)

    caplog.set_level(logging.WARNING)
    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    assert secret not in caplog.text
    assert "code=<redacted>" in caplog.text
    system.assert_called_once()


def test_auto_does_not_reopen_browser_after_callback(monkeypatch) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "desktop")
    callback_event = threading.Event()
    callback_event.set()
    login = browser_login.BrowserLogin(browser_mode="auto")
    monkeypatch.setattr(
        login,
        "_playwright_login_and_wait",
        Mock(side_effect=browser_login.BrowserUnavailableError("target closed")),
    )
    system = Mock()
    monkeypatch.setattr(login, "_system_browser_login_and_wait", system)

    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    system.assert_not_called()


def test_auto_desktop_falls_back_to_external_handoff(monkeypatch) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "desktop")
    callback_event = threading.Event()
    login = browser_login.BrowserLogin(browser_mode="auto")
    login._active_callback_port = 43123
    monkeypatch.setattr(
        login,
        "_playwright_login_and_wait",
        Mock(side_effect=browser_login.BrowserUnavailableError("controlled missing")),
    )
    monkeypatch.setattr(
        login,
        "_system_browser_login_and_wait",
        Mock(side_effect=browser_login.BrowserUnavailableError("system missing")),
    )
    external = Mock()
    monkeypatch.setattr(login, "_external_browser_login_and_wait", external)

    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    external.assert_called_once_with(
        "https://example.invalid/login",
        callback_event,
        reason="browser_unavailable",
    )


def test_auto_wsl_falls_back_to_manual_handoff(monkeypatch) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "wsl")
    callback_event = threading.Event()
    login = browser_login.BrowserLogin(browser_mode="auto")
    login._active_callback_port = 43123
    monkeypatch.setattr(
        login,
        "_wsl_host_browser_login_and_wait",
        Mock(side_effect=browser_login.BrowserUnavailableError("bridge missing")),
    )
    external = Mock()
    monkeypatch.setattr(login, "_external_browser_login_and_wait", external)

    login._browser_login_and_wait("https://example.invalid/login", callback_event)

    external.assert_called_once_with(
        "https://example.invalid/login",
        callback_event,
        reason="wsl_browser_unavailable",
    )


def test_wsl_powershell_launcher_encodes_url_instead_of_command_arguments(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        browser_login.shutil,
        "which",
        lambda name: (
            "/mnt/c/Windows/powershell.exe" if name == "powershell.exe" else None
        ),
    )
    completed = Mock(returncode=0)
    run = Mock(return_value=completed)
    monkeypatch.setattr(browser_login.subprocess, "run", run)
    callback_event = threading.Event()
    callback_event.set()
    login_url = "https://example.invalid/apply?port=43123&appid=1007&code=abc"
    login = browser_login.BrowserLogin(browser_mode="auto")

    login._wsl_host_browser_login_and_wait(login_url, callback_event)

    command = run.call_args.args[0]
    assert "-EncodedCommand" in command
    assert login_url not in command
    encoded = command[command.index("-EncodedCommand") + 1]
    decoded = browser_login.base64.b64decode(encoded).decode("utf-16le")
    assert decoded == f"Start-Process -FilePath '{login_url}'"


def test_wsl_launcher_failure_does_not_expose_one_time_url(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_login.shutil,
        "which",
        lambda name: "/usr/bin/wslview" if name == "wslview" else None,
    )
    secret = "sensitive-csrf-value"
    login_url = f"https://example.invalid/apply?code={secret}"
    monkeypatch.setattr(
        browser_login.subprocess,
        "run",
        Mock(
            side_effect=browser_login.subprocess.TimeoutExpired(
                ["/usr/bin/wslview", login_url],
                15,
            )
        ),
    )
    login = browser_login.BrowserLogin(browser_mode="auto")

    try:
        login._wsl_host_browser_login_and_wait(login_url, threading.Event())
    except browser_login.BrowserUnavailableError as exc:
        assert secret not in str(exc)
        assert "TimeoutExpired" in str(exc)
    else:
        raise AssertionError("failed WSL launcher should request external handoff")


def test_external_handoff_completes_through_loopback_callback(monkeypatch) -> None:
    monkeypatch.setattr(browser_login, "_browser_environment", lambda: "ssh")
    events = []
    event_ready = threading.Event()
    result = {}

    def capture_event(event) -> None:
        events.append(event)
        event_ready.set()

    login = browser_login.BrowserLogin(
        browser_mode="external",
        callback_timeout=5,
        event_callback=capture_event,
    )

    def run_login() -> None:
        try:
            result["token"] = login.login()
        except Exception as exc:  # pragma: no cover - assertion below preserves detail
            result["error"] = exc

    worker = threading.Thread(target=run_login, daemon=True)
    worker.start()
    assert event_ready.wait(timeout=2)
    event = events[0]
    parsed = urlparse(str(event["verification_uri"]))
    code = parse_qs(parsed.query)["code"][0]
    connection = http.client.HTTPConnection(
        str(event["callback_host"]),
        int(event["callback_port"]),
        timeout=5,
    )
    connection.request(
        "POST",
        "/callback",
        urlencode({"tempToken": "external-token", "code": code}),
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    response = connection.getresponse()
    response.read()
    connection.close()
    worker.join(timeout=3)

    assert response.status == 302
    assert result == {"token": "external-token"}
    assert event["method"] == "ssh_loopback"
    assert login._active_callback_port == 0


def test_valid_callback_wins_over_browser_shutdown_error(monkeypatch) -> None:
    login = browser_login.BrowserLogin(browser_mode="system")

    def callback_then_fail(login_url, callback_event) -> None:
        parsed = urlparse(login_url)
        query = parse_qs(parsed.query)
        connection = http.client.HTTPConnection(
            browser_login._CALLBACK_HOST,
            int(query["port"][0]),
            timeout=5,
        )
        connection.request(
            "POST",
            "/callback",
            urlencode(
                {
                    "tempToken": "callback-won-token",
                    "code": query["code"][0],
                }
            ),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        assert response.status == 302
        assert callback_event.wait(timeout=2)
        raise RuntimeError("target closed after callback")

    monkeypatch.setattr(login, "_browser_login_and_wait", callback_then_fail)

    assert login.login() == "callback-won-token"


def test_fixed_callback_port_reports_bind_conflict() -> None:
    handler = browser_login._make_callback_handler(
        "occupied",
        {},
        threading.Event(),
    )
    occupied = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    port = occupied.server_address[1]
    login = browser_login.BrowserLogin(
        browser_mode="external",
        callback_port=port,
        callback_timeout=1,
    )

    try:
        try:
            login.login()
        except RuntimeError as exc:
            assert f"callback port {port}" in str(exc)
        else:
            raise AssertionError("occupied callback port should fail before login")
    finally:
        occupied.server_close()


def test_controlled_system_browser_prefers_edge() -> None:
    browser = Mock()
    chromium = Mock()
    chromium.launch.return_value = browser
    playwright = Mock(chromium=chromium)

    result, runtime = browser_login._launch_controlled_browser(
        playwright,
        "system_controlled",
        allow_fallback=False,
        smoke_test=True,
    )

    assert result is browser
    assert runtime == "msedge"
    chromium.launch.assert_called_once()
    assert chromium.launch.call_args.kwargs["channel"] == "msedge"
    assert "--headless=new" in chromium.launch.call_args.kwargs["args"]


def test_controlled_system_browser_falls_back_to_chrome() -> None:
    browser = Mock()
    chromium = Mock()
    chromium.launch.side_effect = [RuntimeError("edge missing"), browser]
    playwright = Mock(chromium=chromium)

    result, runtime = browser_login._launch_controlled_browser(
        playwright,
        "system_controlled",
        allow_fallback=False,
    )

    assert result is browser
    assert runtime == "chrome"
    assert [call.kwargs["channel"] for call in chromium.launch.call_args_list] == [
        "msedge",
        "chrome",
    ]


def test_missing_bundled_chromium_falls_back_for_existing_settings(
    tmp_path,
) -> None:
    browser = Mock()
    chromium = Mock()
    chromium.executable_path = str(tmp_path / "missing-chromium.exe")
    chromium.launch.return_value = browser
    playwright = Mock(chromium=chromium)

    result, runtime = browser_login._launch_controlled_browser(
        playwright,
        "playwright",
        allow_fallback=True,
    )

    assert result is browser
    assert runtime == "msedge"
    chromium.launch.assert_called_once()
    assert chromium.launch.call_args.kwargs["channel"] == "msedge"


def test_sensitive_callback_logging_requires_explicit_switch(caplog) -> None:
    callback_data = {}
    callback_event = threading.Event()
    handler = browser_login._make_callback_handler(
        "expected-code", callback_data, callback_event
    )
    server = ThreadingHTTPServer((browser_login._CALLBACK_HOST, 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    secret = "explicit-sensitive-token"

    try:
        set_sensitive_logging(True)
        caplog.set_level(logging.DEBUG)
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = urlencode({"tempToken": secret, "code": "expected-code"})
        connection.request(
            "POST",
            "/callback",
            body,
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 302
        assert secret in caplog.text
    finally:
        set_sensitive_logging(False)
        server.shutdown()
        server.server_close()


def test_unknown_browser_mode_is_rejected() -> None:
    login = browser_login.BrowserLogin(browser_mode="unknown")

    try:
        login._browser_login_and_wait("https://example.invalid", threading.Event())
    except RuntimeError as exc:
        assert "Unsupported browser mode" in str(exc)
    else:
        raise AssertionError("unsupported browser mode should fail")


def test_callback_wait_can_be_cancelled() -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    login = browser_login.BrowserLogin(browser_mode="system", cancel_event=cancel_event)

    try:
        login._wait_for_callback(threading.Event())
    except OperationCancelled:
        pass
    else:
        raise AssertionError("cancelled callback wait should stop")
