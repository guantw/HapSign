"""运行目录、用户数据目录与外部工具链发现。

该模块不依赖 GUI，CLI、桌面版和 PyInstaller 便携版共用同一套规则。
所有平台差异集中在这里，避免业务流程散落 Windows 专用路径。
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "HapSign"


def platform_tag() -> str:
    """返回便携资源目录使用的平台标识。"""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def application_dir() -> Path:
    """返回应用所在目录；冻结后为可执行文件目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """返回便携版资源根目录，可通过环境变量覆盖。"""
    override = os.environ.get("HAPSIGN_RESOURCE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return application_dir() / "resources"


def app_data_dir() -> Path:
    """Return the shared per-user data root used by GUI and every CLI edition."""
    override = os.environ.get("HAPSIGN_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / APP_NAME
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / APP_NAME.lower()


@dataclass(frozen=True)
class ToolchainPaths:
    """签名与安装所需外部工具路径。"""

    java: Path
    keytool: Path
    hap_sign_tool: Path
    hdc: Path
    source: str

    def missing(
        self,
        *,
        require_signing: bool = True,
        require_hdc: bool = True,
    ) -> list[str]:
        """返回缺少或不可执行的工具。

        ``require_hdc=False`` 用于仅签名且调用方已经提供设备 UDID，或复用已有
        Profile 的场景。默认值保持旧版“签名并安装”的检查语义。
        """
        required = [("HDC", self.hdc)] if require_hdc else []
        if require_signing:
            required.extend(
                [
                    ("Java", self.java),
                    ("keytool", self.keytool),
                    ("hap-sign-tool.jar", self.hap_sign_tool),
                ]
            )
        problems: list[str] = []
        for name, path in required:
            if not path.is_file():
                problems.append(f"{name}: {path}")
            elif (
                name != "hap-sign-tool.jar"
                and os.name != "nt"
                and not os.access(path, os.X_OK)
            ):
                problems.append(f"{name}: {path}（文件不可执行）")
        return problems


def _executable_name(name: str) -> str:
    return f"{name}.exe" if platform.system() == "Windows" else name


def _from_portable_resources() -> ToolchainPaths:
    root = resource_dir() / "toolchain" / platform_tag()
    # 新版公开工具链使用中性的 runtime/；旧便携包仍兼容 jbr/。
    runtime_root = root / "runtime"
    if not runtime_root.is_dir():
        runtime_root = root / "jbr"
    return ToolchainPaths(
        java=runtime_root / "bin" / _executable_name("java"),
        keytool=runtime_root / "bin" / _executable_name("keytool"),
        hap_sign_tool=root / "lib" / "hap-sign-tool.jar",
        hdc=root / "bin" / _executable_name("hdc"),
        source="portable",
    )


def _from_deveco_home(home: Path, source: str) -> ToolchainPaths:
    toolchains = home / "sdk" / "default" / "openharmony" / "toolchains"
    # DevEco Studio 的 macOS 应用包把 JBR 放在 Contents/Home 下；Windows
    # 和 Linux 的发行版则直接使用 jbr/bin。
    runtime_root = home / "jbr"
    if platform.system() == "Darwin":
        runtime_root = runtime_root / "Contents" / "Home"
    return ToolchainPaths(
        java=runtime_root / "bin" / _executable_name("java"),
        keytool=runtime_root / "bin" / _executable_name("keytool"),
        hap_sign_tool=toolchains / "lib" / "hap-sign-tool.jar",
        hdc=toolchains / _executable_name("hdc"),
        source=source,
    )


def _deveco_home_candidates() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    configured = os.environ.get("DEVECO_HOME")
    if configured:
        candidates.append((Path(configured).expanduser(), "DEVECO_HOME"))

    system = platform.system()
    if system == "Windows":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        candidates.extend(
            [
                (program_files / "Huawei" / "DevEco Studio", "DevEco Studio"),
                (
                    Path(r"D:\Program Files\Huawei\DevEco Studio"),
                    "DevEco Studio",
                ),
            ]
        )
    elif system == "Darwin":
        candidates.extend(
            [
                (
                    Path("/Applications/DevEco-Studio.app/Contents"),
                    "DevEco Studio",
                ),
                (
                    Path("/Applications/DevEco Studio.app/Contents"),
                    "DevEco Studio",
                ),
            ]
        )
    else:
        candidates.extend(
            [
                (Path("/opt/DevEco-Studio"), "DevEco Studio"),
                (Path("/opt/Huawei/DevEco-Studio"), "DevEco Studio"),
                (Path.home() / "DevEco-Studio", "DevEco Studio"),
                (Path.home() / "Huawei" / "DevEco-Studio", "DevEco Studio"),
            ]
        )
    return candidates


def _from_path_environment() -> ToolchainPaths:
    """从 JAVA_HOME、PATH 和签名器环境变量组合源码运行工具链。"""

    runtime_bin = None
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        runtime_bin = Path(java_home).expanduser() / "bin"

    def command(name: str) -> Path:
        executable = _executable_name(name)
        if runtime_bin is not None and name in {"java", "keytool"}:
            candidate = runtime_bin / executable
            if candidate.is_file():
                return candidate
        discovered = shutil.which(executable)
        return Path(discovered) if discovered else Path(executable)

    signer = os.environ.get("HAPSIGN_HAP_SIGN_TOOL")
    return ToolchainPaths(
        java=command("java"),
        keytool=command("keytool"),
        hap_sign_tool=(
            Path(signer).expanduser() if signer else Path("hap-sign-tool.jar")
        ),
        hdc=command("hdc"),
        source="JAVA_HOME/PATH",
    )


def _with_direct_overrides(paths: ToolchainPaths) -> ToolchainPaths:
    def override(name: str, current: Path) -> Path:
        value = os.environ.get(name)
        return Path(value).expanduser() if value else current

    overrides = {
        name
        for name in (
            "HAPSIGN_JAVA",
            "HAPSIGN_KEYTOOL",
            "HAPSIGN_HAP_SIGN_TOOL",
            "HAPSIGN_HDC",
        )
        if os.environ.get(name)
    }
    return ToolchainPaths(
        java=override("HAPSIGN_JAVA", paths.java),
        keytool=override("HAPSIGN_KEYTOOL", paths.keytool),
        hap_sign_tool=override("HAPSIGN_HAP_SIGN_TOOL", paths.hap_sign_tool),
        hdc=override("HAPSIGN_HDC", paths.hdc),
        source="environment overrides" if overrides else paths.source,
    )


def discover_toolchain() -> ToolchainPaths:
    """发现便携资源、DevEco Studio，最后组合 JAVA_HOME/PATH。"""
    candidates = [_from_portable_resources()]
    candidates.extend(
        _from_deveco_home(home, source) for home, source in _deveco_home_candidates()
    )
    candidates.append(_from_path_environment())

    overridden = [_with_direct_overrides(item) for item in candidates]
    complete = next((item for item in overridden if not item.missing()), None)
    if complete is not None:
        return complete

    # 未完整安装时返回现存文件最多的一组，让错误信息指向最可能的安装位置。
    return max(
        overridden,
        key=lambda item: sum(
            path.is_file()
            for path in (item.java, item.keytool, item.hap_sign_tool, item.hdc)
        ),
    )
