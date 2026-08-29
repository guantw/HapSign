"""HapSign 桌面配置与数据目录解析。"""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path

from hapsign.runtime import APP_NAME, application_dir

LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
STORAGE_MODES = {"program", "appdata", "custom"}
BROWSER_MODES = {"system_controlled", "playwright", "system"}


@dataclass(frozen=True)
class AppSettings:
    """可持久化的桌面设置。"""

    log_level: str = "INFO"
    signing_storage: str = "program"
    custom_signing_dir: str = ""
    browser_mode: str = "system_controlled"
    log_sensitive_data: bool = False
    keep_signed_hap: bool = True


def config_file_path() -> Path:
    """配置文件默认与可执行文件放在一起，保持便携语义。"""
    override = os.environ.get("HAPSIGN_CONFIG_FILE")
    if override:
        return Path(override).expanduser().resolve()
    return application_dir() / "hapsign-config.json"


def user_local_data_dir() -> Path:
    """返回当前平台的用户本地应用数据目录。"""
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / APP_NAME


def signing_files_dir(settings: AppSettings) -> Path:
    """按专用环境变量、数据根目录和桌面设置返回签名状态目录。"""
    signing_override = os.environ.get("HAPSIGN_SIGNING_DIR")
    if signing_override:
        return Path(signing_override).expanduser().resolve()
    override = os.environ.get("HAPSIGN_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve() / "signing_files"
    if settings.signing_storage == "appdata":
        return user_local_data_dir() / "signing_files"
    if settings.signing_storage == "custom" and settings.custom_signing_dir:
        return Path(settings.custom_signing_dir).expanduser().resolve()
    return application_dir() / "signing_files"


def log_directory() -> Path:
    """日志固定优先写入程序目录，便于便携迁移和问题定位。"""
    return application_dir() / "logs"


def signed_haps_dir() -> Path:
    """返回默认签名产物目录，允许用专用环境变量覆盖。"""
    override = os.environ.get("HAPSIGN_SIGNED_HAPS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return application_dir() / "signed_haps"


def _validated(data: dict) -> AppSettings:
    log_level = str(data.get("log_level", "INFO")).upper()
    if log_level not in LOG_LEVELS:
        log_level = "INFO"
    storage = str(data.get("signing_storage", "program")).lower()
    if storage not in STORAGE_MODES:
        storage = "program"
    browser_mode = str(data.get("browser_mode", "system_controlled")).lower()
    if browser_mode not in BROWSER_MODES:
        browser_mode = "system_controlled"
    keep_signed_hap = data.get("keep_signed_hap", True)
    if not isinstance(keep_signed_hap, bool):
        keep_signed_hap = True
    return AppSettings(
        log_level=log_level,
        signing_storage=storage,
        custom_signing_dir=str(data.get("custom_signing_dir", "")),
        browser_mode=browser_mode,
        log_sensitive_data=data.get("log_sensitive_data", False) is True,
        keep_signed_hap=keep_signed_hap,
    )


def load_settings() -> AppSettings:
    """读取 JSON 设置；缺失或损坏时使用安全默认值。"""
    path = config_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    return _validated(data) if isinstance(data, dict) else AppSettings()


def save_settings(settings: AppSettings) -> Path:
    """原子保存 JSON 设置并返回文件路径。"""
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path
