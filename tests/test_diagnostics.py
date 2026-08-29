"""持久诊断日志配置测试。"""

import logging

from hapsign import diagnostics
from hapsign.settings import AppSettings


def test_file_logging_reconfigures_single_handler_and_sensitive_switch(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(diagnostics, "log_directory", lambda: tmp_path / "logs")
    root = logging.getLogger()
    previous_level = root.level

    try:
        first_path = diagnostics.configure_file_logging(
            AppSettings(log_level="DEBUG", log_sensitive_data=False)
        )
        logging.getLogger("hapsign.test").warning("diagnostic marker")
        handlers = [
            handler
            for handler in root.handlers
            if getattr(handler, diagnostics._HANDLER_MARKER, False)
        ]
        for handler in handlers:
            handler.flush()

        assert first_path == tmp_path / "logs" / "hapsign.log"
        assert "diagnostic marker" in first_path.read_text(encoding="utf-8")
        assert len(handlers) == 1
        assert diagnostics.sensitive_logging_enabled() is False

        diagnostics.configure_file_logging(
            AppSettings(log_level="ERROR", log_sensitive_data=True)
        )
        handlers = [
            handler
            for handler in root.handlers
            if getattr(handler, diagnostics._HANDLER_MARKER, False)
        ]
        assert len(handlers) == 1
        assert handlers[0].level == logging.ERROR
        assert diagnostics.sensitive_logging_enabled() is True
    finally:
        for handler in list(root.handlers):
            if getattr(handler, diagnostics._HANDLER_MARKER, False):
                root.removeHandler(handler)
                handler.close()
        root.setLevel(previous_level)
        diagnostics.set_sensitive_logging(False)


def test_redact_sensitive_text_hides_tokens_and_bearer_headers() -> None:
    diagnostics.set_sensitive_logging(False)

    text = diagnostics.redact_sensitive_text(
        "tempToken=TEMP accessToken=ACCESS Authorization: Bearer BEARER"
    )

    assert "TEMP" not in text
    assert "ACCESS" not in text
    assert "BEARER" not in text
    assert "tempToken=<redacted>" in text
    assert "accessToken=<redacted>" in text
    assert "Authorization: Bearer <redacted>" in text


def test_redact_sensitive_text_hides_device_udid() -> None:
    diagnostics.set_sensitive_logging(False)
    udid = "A" * 64

    text = diagnostics.redact_sensitive_text(
        f"Device not found in list: {udid}; request_id={udid}0"
    )

    assert udid not in text.split(";", 1)[0]
    assert "Device not found in list: <redacted-udid>" in text
    # 不应截断更长的十六进制诊断标识。
    assert f"request_id={udid}0" in text


def test_device_udid_validation_uses_the_same_boundary_as_redaction() -> None:
    assert diagnostics.is_valid_device_udid("  " + "A" * 64 + "\n")
    assert diagnostics.is_valid_device_udid("a" * 64)
    assert not diagnostics.is_valid_device_udid("A" * 63)
    assert not diagnostics.is_valid_device_udid("G" * 64)
    assert not diagnostics.is_valid_device_udid(None)
