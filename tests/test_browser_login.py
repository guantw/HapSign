"""本地登录回调的安全回归测试。"""

import http.client
import logging
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock
from urllib.parse import urlencode

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
