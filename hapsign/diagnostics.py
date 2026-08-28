"""持久化诊断日志。"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from hapsign import __version__
from hapsign.runtime import platform_tag
from hapsign.settings import AppSettings, log_directory, user_local_data_dir

_HANDLER_MARKER = "_hapsign_file_handler"
_sensitive_logging_enabled = False

_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<key>\b(?:temp[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"jwt[_-]?token|oauth2[_-]?token|keystore[_-]?password|password|passwd|"
    r"secret|token|code)\b)"
    r"(?P<separator>\s*['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<value>[^&,\s}'\"\]\)]+)"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(?P<key>\bauthorization\b)"
    r"(?P<separator>\s*['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<scheme>Bearer\s+)?"
    r"(?P<value>[^&,\s}'\"\]\)]+)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)(?P<prefix>\bBearer\s+)(?P<value>[^\s,}'\"\]\)]+)")
_DEVICE_UDID_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def set_sensitive_logging(enabled: bool) -> None:
    """控制是否允许诊断日志包含 token 及完整网络载荷。"""
    global _sensitive_logging_enabled
    _sensitive_logging_enabled = bool(enabled)


def sensitive_logging_enabled() -> bool:
    """返回敏感诊断开关状态。"""
    return _sensitive_logging_enabled


def redact_sensitive_text(value: object) -> str:
    """脱敏异常文本中的令牌、密码、授权参数和设备 UDID。

    敏感诊断开关是显式选择，开启后保留原始文本以便定位协议问题；默认日志
    必须避免把认证信息写入控制台、文件或 GUI 日志窗口。
    """
    text = str(value)
    if sensitive_logging_enabled():
        return text

    text = _AUTHORIZATION_RE.sub(
        lambda match: (
            f"{match.group('key')}{match.group('separator')}"
            f"{match.group('scheme') or ''}<redacted>"
        ),
        text,
    )
    text = _SENSITIVE_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('separator')}<redacted>",
        text,
    )
    text = _BEARER_TOKEN_RE.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        text,
    )
    return _DEVICE_UDID_RE.sub("<redacted-udid>", text)


def configure_file_logging(settings: AppSettings) -> Path:
    """配置滚动日志；程序目录不可写时回退到用户本地目录。"""
    set_sensitive_logging(settings.log_sensitive_data)
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root_logger.removeHandler(handler)
            handler.close()

    preferred = log_directory()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        log_path = preferred / "hapsign.log"
        handler = RotatingFileHandler(
            log_path,
            maxBytes=4 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError:
        fallback = user_local_data_dir() / "logs"
        fallback.mkdir(parents=True, exist_ok=True)
        log_path = fallback / "hapsign.log"
        handler = RotatingFileHandler(
            log_path,
            maxBytes=4 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )

    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(getattr(logging, settings.log_level))
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-7s "
            "[%(threadName)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(handler)
    # 由各 handler 决定输出级别；根 logger 保留 DEBUG 才能让文件级别即时生效。
    root_logger.setLevel(logging.DEBUG)
    logging.captureWarnings(True)
    logging.getLogger(__name__).info(
        "HapSign 启动：version=%s platform=%s log_level=%s sensitive=%s",
        __version__,
        platform_tag(),
        settings.log_level,
        settings.log_sensitive_data,
    )
    if settings.log_sensitive_data:
        logging.getLogger(__name__).warning(
            "敏感诊断已开启：日志可能包含 token、用户标识及完整 API 请求/响应"
        )
    return log_path
