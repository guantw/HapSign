"""Agent 友好的 HapSign 命令行入口。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import platform
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from hapsign import __version__
from hapsign.cancellation import OperationCancelled
from hapsign.config import DEVICE_TYPE_PHONE
from hapsign.diagnostics import redact_sensitive_text
from hapsign.migrations import (
    breaking_changes,
    cache_compatibility_warning,
    legacy_state_warning,
    migrate_legacy_cache,
)
from hapsign.pipeline import SignPipeline
from hapsign.runtime import (
    ToolchainPaths,
    application_dir,
    discover_toolchain,
    platform_tag,
)
from hapsign.settings import (
    config_file_path,
    load_settings,
    signed_haps_dir,
    signing_files_dir,
)
from hapsign.signing.hap_inspect import is_hap_signed
from hapsign.signing.installer import Installer
from hapsign.token import secure_token_cache

_BROWSER_MODES = ("system", "system_controlled", "playwright")


def _default_browser_mode() -> str:
    """返回可复现的 CLI 浏览器默认值，并允许显式环境变量覆盖。"""
    configured = os.environ.get("HAPSIGN_BROWSER", "system_controlled").lower()
    return configured if configured in _BROWSER_MODES else "system_controlled"


COMMANDS = {
    "auth",
    "deploy",
    "devices",
    "doctor",
    "inspect",
    "install",
    "migrate-cache",
    "sign",
}
EXIT_OK = 0
EXIT_OPERATION_FAILED = 1
EXIT_USAGE = 2
EXIT_CANCELLED = 130


def detect_bundle_name(hap_path: str) -> str:
    """从 HAP 文件内的 module.json 提取 bundleName。"""
    with zipfile.ZipFile(hap_path) as archive:
        for name in archive.namelist():
            if name.lower() == "module.json":
                data = json.loads(archive.read(name))
                try:
                    return str(data["app"]["bundleName"])
                except (KeyError, TypeError) as exc:
                    raise ValueError("module.json 中未找到 app.bundleName") from exc
    raise ValueError("HAP 文件中未找到 module.json，无法提取 bundleName")


def _formatter(prog: str) -> argparse.HelpFormatter:
    return argparse.RawDescriptionHelpFormatter(prog, max_help_position=30, width=100)


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="stdout 只输出单行 JSON；日志和诊断写到 stderr",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出 DEBUG 日志")


def _add_state_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        default=str(signing_files_dir(load_settings())),
        help=(
            "Token 与默认签名材料根目录；默认按应用配置；环境变量 HAPSIGN_SIGNING_DIR"
        ),
    )


def _nonempty_serial(value: str) -> str:
    """规范化显式 HDC serial，禁止空值退回隐式目标选择。"""
    serial = value.strip()
    if not serial:
        raise argparse.ArgumentTypeError("--serial 不能为空")
    return serial


def _add_hap_identity_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hap", required=True, help="HAP 文件的绝对或相对路径")
    parser.add_argument(
        "--bundle-name",
        default=None,
        help="覆盖 module.json 中的 bundleName；通常不需要指定",
    )


def _add_path_options(
    parser: argparse.ArgumentParser,
    *,
    include_exact_output: bool,
) -> None:
    _add_state_option(parser)
    parser.add_argument(
        "--work-dir",
        default="",
        help="当前 bundle 的签名材料目录；默认 <state-dir>/<bundle>",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help=(
            "默认签名 HAP 输出目录；默认应用目录 signed_haps/；"
            "环境变量 HAPSIGN_SIGNED_HAPS_DIR"
        ),
    )
    if include_exact_output:
        parser.add_argument(
            "--output",
            default="",
            help="签名 HAP 的精确输出路径；优先于 --output-dir",
        )
        parser.add_argument(
            "--overwrite-output",
            action="store_true",
            help="允许覆盖 --output 指定的已有文件",
        )


def _add_capability_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--enable-capability",
        action="store_true",
        help="尝试使用 Real Profile（APL=system_basic）；需要已注册 AGC 应用",
    )


def _add_signing_options(
    parser: argparse.ArgumentParser,
    *,
    require_serial: bool,
) -> None:
    _add_hap_identity_options(parser)
    if require_serial:
        parser.add_argument(
            "--serial",
            required=True,
            type=_nonempty_serial,
            help="hdc list targets 返回的目标序列号；安装时必须显式指定",
        )
        parser.set_defaults(device_udid="")
    else:
        target = parser.add_mutually_exclusive_group()
        target.add_argument(
            "--serial",
            type=_nonempty_serial,
            default=None,
            help="hdc list targets 返回的目标序列号",
        )
        target.add_argument(
            "--device-udid",
            default="",
            help="可信的 64 位设备 UDID；可在不连接本机设备时申请 Profile",
        )
    parser.add_argument("--country", default="CN", help="华为账号国家码；默认 CN")
    parser.add_argument(
        "--device-type",
        default=DEVICE_TYPE_PHONE,
        help="签名平台注册的设备类型码；默认 4（手机/平板/2in1）",
    )
    _add_path_options(parser, include_exact_output=True)
    parser.add_argument(
        "--browser",
        choices=_BROWSER_MODES,
        default=_default_browser_mode(),
        help="首次认证使用的浏览器模式；默认 system_controlled",
    )
    _add_capability_option(parser)
    parser.add_argument(
        "--refresh-token",
        action="store_true",
        help="删除 Token 缓存并重新浏览器认证，同时刷新签名材料",
    )
    parser.add_argument(
        "--refresh-signing",
        action="store_true",
        help="重新申请证书/Profile，但复用有效 Auth Token",
    )
    _add_output_options(parser)


def build_parser() -> argparse.ArgumentParser:
    """创建包含 Agent 子命令与明确退出码的参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="hapsign",
        description=(
            "HarmonyOS HAP 账号认证、设备枚举、调试签名与安装 CLI。\n"
            "面向 Agent：所有执行命令支持 --json，凭据永不写入 stdout。"
        ),
        formatter_class=_formatter,
        epilog="""\
典型 Agent 流程:
  hapsign doctor --json
  hapsign inspect --hap app-unsigned.hap --json
  hapsign devices list --connected-only --json
  hapsign auth status --json
  hapsign auth --json
  hapsign sign --hap app-unsigned.hap --serial <serial> --json
  hapsign install --hap app-signed.hap --serial <serial> --json
  hapsign deploy --hap app-unsigned.hap --serial <serial> --json

退出码:
  0    成功
  1    运行失败（认证、签名、HDC 或安装失败）
  2    参数或输入文件无效
  130  用户取消
""",
    )
    parser.add_argument("--version", action="version", version=f"hapsign {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    doctor = subparsers.add_parser(
        "doctor",
        help="检查平台、工具链、路径和兼容性变更；不修改本地状态",
        formatter_class=_formatter,
    )
    _add_state_option(doctor)
    doctor.add_argument("--output-dir", default="", help="覆盖诊断中的默认产物目录")
    _add_output_options(doctor)

    inspect = subparsers.add_parser(
        "inspect",
        help="只读检查 HAP、解析路径并报告适用的迁移警告",
        formatter_class=_formatter,
    )
    _add_hap_identity_options(inspect)
    _add_path_options(inspect, include_exact_output=False)
    _add_capability_option(inspect)
    _add_output_options(inspect)

    migrate = subparsers.add_parser(
        "migrate-cache",
        help="在用户确认旧 Profile 类型后迁移当天缓存元数据",
        formatter_class=_formatter,
    )
    _add_hap_identity_options(migrate)
    _add_state_option(migrate)
    migrate.add_argument(
        "--work-dir",
        default="",
        help="当前 bundle 的旧签名材料目录；默认 <state-dir>/<bundle>",
    )
    migrate.add_argument(
        "--profile-type",
        required=True,
        choices=("normal", "system-basic"),
        help="用户确认的旧 Profile 类型",
    )
    _add_output_options(migrate)

    auth = subparsers.add_parser(
        "auth",
        help="登录或查看本地 Token 缓存状态；不检测设备、不签名、不安装",
        description=(
            "认证华为开发者账号并保存 Token。Auth Token 属于账号/Team，不绑定设备；"
            "设备绑定发生在后续生成的 debug Profile 中。"
        ),
        formatter_class=_formatter,
        epilog="""\
示例:
  hapsign auth --json                 # 有缓存则复用，否则打开浏览器
  hapsign auth --refresh --json       # 强制重新浏览器认证
  hapsign auth status --json          # 只检查本地缓存，不验证服务端有效性
""",
    )
    auth.add_argument(
        "auth_action",
        nargs="?",
        choices=("login", "status"),
        default="login",
        help="login（默认）或 status",
    )
    auth.add_argument("--country", default="CN", help="华为账号国家码；默认 CN")
    auth.add_argument(
        "--browser",
        choices=_BROWSER_MODES,
        default=_default_browser_mode(),
        help="认证浏览器模式；默认 system_controlled",
    )
    auth.add_argument(
        "--refresh",
        action="store_true",
        help="忽略并删除现有缓存，强制重新浏览器认证",
    )
    _add_state_option(auth)
    _add_output_options(auth)

    devices = subparsers.add_parser(
        "devices",
        help="列出 HDC targets，供 Agent 选择 --serial",
        description=(
            "列出 HDC 连接目标。JSON 会标记 connected、physical_candidate "
            "和 likely_emulator；"
            "serial 是 HDC 目标标识，不是 Profile 使用的 UDID。"
        ),
        formatter_class=_formatter,
        epilog="""\
示例:
  hapsign devices list --json
  hapsign devices list --connected-only --json
""",
    )
    devices.add_argument(
        "devices_action",
        nargs="?",
        choices=("list",),
        default="list",
        help="当前支持 list（默认）",
    )
    devices.add_argument(
        "--connected-only", action="store_true", help="只返回 Connected targets"
    )
    _add_output_options(devices)

    sign = subparsers.add_parser(
        "sign",
        help="签名 HAP 但不安装；可显式选择设备或复用兼容缓存",
        description=(
            "为 --serial/--device-udid 对应设备生成 debug Profile 并签名；"
            "未指定设备时可复用兼容缓存或从当前 HDC 目标读取 UDID。"
            "已有 Auth Token 会复用；"
            "同一 bundle 切换设备时会自动丢弃不匹配的 Profile 缓存。"
        ),
        formatter_class=_formatter,
        epilog="""\
示例:
  hapsign sign --hap app-unsigned.hap --serial <serial> --json
  hapsign sign --hap app.hap --serial <serial> --output-dir ./signed --json
""",
    )
    _add_signing_options(sign, require_serial=False)

    install = subparsers.add_parser(
        "install",
        help="把已经签名的 HAP 覆盖安装到指定设备",
        description=(
            "只接受包含 Hap Signing Block 的已签名 HAP。安装使用 hdc -t <serial> "
            "install -r，并在安装后通过 bm dump 确认 bundle。"
        ),
        formatter_class=_formatter,
        epilog="""\
示例:
  hapsign install --hap app-signed.hap --serial <serial> --json
""",
    )
    _add_hap_identity_options(install)
    install.add_argument(
        "--serial",
        required=True,
        type=_nonempty_serial,
        help="目标 HDC 序列号",
    )
    _add_output_options(install)

    deploy = subparsers.add_parser(
        "deploy",
        help="自动认证、签名并安装；已签名 HAP 会直接安装",
        description=(
            "端到端命令。未签名 HAP：认证 → 证书/Profile → 签名 → 安装；"
            "已签名 HAP：跳过认证和签名，直接覆盖安装。"
        ),
        formatter_class=_formatter,
        epilog="""\
示例:
  hapsign deploy --hap app-unsigned.hap --serial <serial> --json
  hapsign deploy --hap app-signed.hap --serial <serial> --json
""",
    )
    _add_signing_options(deploy, require_serial=True)
    return parser


def _command_from_args(argv: Sequence[str]) -> str:
    return next((item for item in argv if item in COMMANDS), "unknown")


def _tool_status(path: Path, *, executable: bool) -> dict[str, object]:
    exists = path.is_file()
    can_execute = exists and (
        os.name == "nt" or not executable or os.access(path, os.X_OK)
    )
    return {
        "path": str(path.expanduser().absolute()),
        "exists": exists,
        "executable": can_execute,
    }


def _resolved_directories(
    *,
    state_override: str = "",
    output_override: str = "",
) -> tuple[Path, Path]:
    settings = load_settings()
    state = (
        Path(state_override).expanduser().resolve()
        if state_override
        else signing_files_dir(settings)
    )
    output = (
        Path(output_override).expanduser().resolve()
        if output_override
        else signed_haps_dir()
    )
    return state, output


def _work_directory(state_dir: Path, work_override: str, bundle_name: str) -> Path:
    if work_override:
        return Path(work_override).expanduser().resolve()
    return state_dir / bundle_name


def doctor_report(
    toolchain: ToolchainPaths | None = None,
    *,
    state_dir: str = "",
    output_dir: str = "",
) -> dict[str, object]:
    """返回适合 agent 判断下一步动作的跨平台诊断结果。"""
    selected = toolchain or discover_toolchain()
    resolved_state, resolved_output = _resolved_directories(
        state_override=state_dir,
        output_override=output_dir,
    )
    signing_problems = selected.missing(require_signing=True, require_hdc=False)
    hdc_problems = selected.missing(require_signing=False, require_hdc=True)
    return {
        "ok": not signing_problems and not hdc_problems,
        "command": "doctor",
        "platform": platform_tag(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "toolchain_source": selected.source,
        "paths": {
            "application_dir": str(application_dir()),
            "current_working_dir": str(Path.cwd()),
            "config_file": str(config_file_path()),
            "state_dir": str(resolved_state),
            "output_dir": str(resolved_output),
        },
        "breaking_changes": breaking_changes(),
        "capabilities": {
            "signing": {"ok": not signing_problems, "problems": signing_problems},
            "device": {"ok": not hdc_problems, "problems": hdc_problems},
        },
        "tools": {
            "java": _tool_status(selected.java, executable=True),
            "keytool": _tool_status(selected.keytool, executable=True),
            "hap_sign_tool": _tool_status(
                selected.hap_sign_tool,
                executable=False,
            ),
            "hdc": _tool_status(selected.hdc, executable=True),
        },
    }


def _parse_args(
    parser: argparse.ArgumentParser, argv: Sequence[str]
) -> argparse.Namespace | int:
    """解析参数；JSON 模式下把 argparse 错误转换为稳定 JSON。"""
    if "--json" not in argv:
        return parser.parse_args(argv)

    diagnostics = io.StringIO()
    try:
        with contextlib.redirect_stderr(diagnostics):
            return parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:
            raise
        lines = diagnostics.getvalue().strip().splitlines()
        message = lines[-1].split("error:", 1)[-1].strip() if lines else "参数无效"
        _failure(
            _command_from_args(argv),
            message,
            True,
            error_type="invalid_arguments",
        )
        return EXIT_USAGE


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _emit(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        # ASCII JSON avoids Windows console code-page corruption while preserving
        # the original Unicode values after parsing.
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return
    if payload.get("ok"):
        print(payload.get("message", "操作成功"))
        for key in (
            "bundle_name",
            "serial",
            "signed_hap",
            "cache_path",
            "provision_type",
        ):
            value = payload.get(key)
            if value:
                print(f"{key}={value}")
        return
    error = payload.get("error")
    if isinstance(error, dict):
        print(f"错误: {error.get('message', '操作失败')}", file=sys.stderr)


def _failure(
    command: str,
    message: str,
    json_output: bool,
    *,
    error_type: str = "operation_failed",
) -> None:
    safe_message = redact_sensitive_text(message)
    _emit(
        {
            "ok": False,
            "command": command,
            "error": {"type": error_type, "message": safe_message},
        },
        json_output,
    )


def _resolve_hap(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if path.suffix.lower() != ".hap":
        raise ValueError("--hap 必须指向 .hap 文件")
    if not path.is_file():
        raise ValueError(f"HAP 文件不存在: {path}")
    return path


def _resolved_bundle_paths(
    args: argparse.Namespace,
    bundle_name: str,
) -> tuple[Path, Path, Path]:
    state_dir, output_dir = _resolved_directories(
        state_override=args.state_dir,
        output_override=getattr(args, "output_dir", ""),
    )
    work_dir = _work_directory(state_dir, getattr(args, "work_dir", ""), bundle_name)
    return state_dir, work_dir, output_dir


def _run_doctor(args: argparse.Namespace) -> int:
    result = doctor_report(state_dir=args.state_dir, output_dir=args.output_dir)
    result["message"] = "环境诊断完成"
    _emit(result, args.json_output)
    return EXIT_OK if result["ok"] else EXIT_OPERATION_FAILED


def _run_inspect(args: argparse.Namespace) -> int:
    hap_path = _resolve_hap(args.hap)
    bundle_name = args.bundle_name or detect_bundle_name(str(hap_path))
    state_dir, work_dir, output_dir = _resolved_bundle_paths(args, bundle_name)
    signed = is_hap_signed(hap_path)
    warnings: list[dict[str, object]] = []
    if not signed:
        state_warning = legacy_state_warning(
            state_dir,
            work_dir,
            bundle_name=bundle_name,
        )
        if state_warning is not None:
            warnings.append(state_warning)
        warning = cache_compatibility_warning(
            work_dir / "metadata.json",
            bundle_name=bundle_name,
            enable_capability=args.enable_capability,
        )
        if warning is not None:
            warnings.append(warning)
    _emit(
        {
            "ok": True,
            "command": "inspect",
            "message": "HAP 检查完成",
            "platform": platform_tag(),
            "hap": str(hap_path),
            "bundle_name": bundle_name,
            "signed": signed,
            "paths": {
                "state_dir": str(state_dir),
                "work_dir": str(work_dir),
                "output_dir": str(output_dir),
            },
            "migration_warnings": warnings,
        },
        args.json_output,
    )
    return EXIT_OK


def _run_migrate_cache(args: argparse.Namespace) -> int:
    hap_path = _resolve_hap(args.hap)
    bundle_name = args.bundle_name or detect_bundle_name(str(hap_path))
    state_dir = Path(args.state_dir).expanduser().resolve()
    work_dir = _work_directory(state_dir, args.work_dir, bundle_name)
    result = migrate_legacy_cache(
        work_dir / "metadata.json",
        bundle_name=bundle_name,
        enable_capability=args.profile_type == "system-basic",
    )
    _emit(
        {
            "ok": True,
            "command": "migrate-cache",
            "message": "旧签名缓存元数据迁移完成",
            "platform": platform_tag(),
            "hap": str(hap_path),
            "bundle_name": bundle_name,
            "capability_mode": args.profile_type,
            **result,
        },
        args.json_output,
    )
    return EXIT_OK


def _auth_pipeline(args: argparse.Namespace) -> SignPipeline:
    state_dir = str(Path(args.state_dir).expanduser().resolve())
    return SignPipeline(
        hap_path="",
        bundle_name="__auth__",
        work_dir=state_dir,
        state_dir=state_dir,
        country=args.country,
        browser_mode=args.browser,
        keep_signed_hap=False,
        install_after_sign=False,
    )


def _cache_format(cache_path: str) -> str:
    try:
        raw = Path(cache_path).read_bytes()
    except OSError:
        return "missing"
    if secure_token_cache.is_encrypted(raw):
        return "dpapi-v1"
    return "plaintext-json-0600" if os.name != "nt" else "legacy-plaintext"


def _run_auth(args: argparse.Namespace) -> int:
    pipeline = _auth_pipeline(args)
    if args.auth_action == "status":
        status = pipeline.auth_status()
        status.update(
            {
                "ok": True,
                "command": "auth status",
                "message": (
                    "发现认证缓存" if status["authenticated"] else "没有可用的认证缓存"
                ),
                "cache_format": _cache_format(str(status["cache_path"])),
            }
        )
        _emit(status, args.json_output)
        return EXIT_OK

    result = pipeline.authenticate(force_refresh=args.refresh)
    status = pipeline.auth_status()
    if not status.get("authenticated"):
        raise RuntimeError("认证成功，但 Token 缓存未能持久化")
    result.update(
        {
            "ok": True,
            "command": "auth",
            "message": "认证缓存可用",
            "cache_path": str(status["cache_path"]),
            "cache_format": _cache_format(str(status["cache_path"])),
        }
    )
    _emit(result, args.json_output)
    return EXIT_OK


def _run_devices(args: argparse.Namespace) -> int:
    with Installer() as installer:
        targets = installer.list_targets(connected_only=args.connected_only)
    connected_count = sum(bool(target["connected"]) for target in targets)
    payload: dict[str, object] = {
        "ok": True,
        "command": "devices list",
        "message": f"发现 {len(targets)} 个 HDC target",
        "count": len(targets),
        "connected_count": connected_count,
        "targets": targets,
    }
    _emit(payload, args.json_output)
    if not args.json_output:
        for target in targets:
            print("{serial}\t{transport}\t{status}\t{host}".format(**target).rstrip())
    return EXIT_OK


def _build_sign_pipeline(
    args: argparse.Namespace,
    hap_path: Path,
    bundle_name: str,
    *,
    state_dir: Path,
    work_dir: Path,
    output_dir: Path,
    install_after_sign: bool,
) -> SignPipeline:
    return SignPipeline(
        hap_path=str(hap_path),
        bundle_name=bundle_name,
        country=args.country,
        device_type=args.device_type,
        serial=args.serial or "",
        device_udid=args.device_udid,
        work_dir=str(work_dir),
        state_dir=str(state_dir),
        enable_capability=args.enable_capability,
        force_refresh_token=args.refresh_token,
        force_refresh_signing=args.refresh_signing,
        browser_mode=args.browser,
        signed_output_dir=str(output_dir),
        signed_output_path=args.output,
        overwrite_output=args.overwrite_output,
        keep_signed_hap=True,
        install_after_sign=install_after_sign,
    )


def _inspect_installed_bundle(serial: str, bundle_name: str) -> dict[str, str]:
    with Installer(serial=serial) as installer:
        bundle = installer.inspect_bundle(bundle_name)
    if bundle is None:
        raise RuntimeError("安装命令结束后 bm dump 未确认目标 bundle")
    return bundle


def _run_sign_or_deploy(args: argparse.Namespace) -> int:
    if args.overwrite_output and not args.output:
        raise ValueError("--overwrite-output 必须与 --output 一起使用")
    hap_path = _resolve_hap(args.hap)
    bundle_name = args.bundle_name or detect_bundle_name(str(hap_path))
    input_signed = is_hap_signed(hap_path)
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
        if output_path.exists() and not args.overwrite_output:
            raise ValueError(
                f"签名输出已存在：{output_path}；如需覆盖请显式传入 --overwrite-output"
            )
    deploy = args.command == "deploy"
    state_dir, work_dir, output_dir = _resolved_bundle_paths(args, bundle_name)
    migration_warnings: list[dict[str, object]] = []
    if not input_signed:
        candidates = (
            legacy_state_warning(
                state_dir,
                work_dir,
                bundle_name=bundle_name,
            ),
            cache_compatibility_warning(
                work_dir / "metadata.json",
                bundle_name=bundle_name,
                enable_capability=args.enable_capability,
            ),
        )
        for warning in candidates:
            if warning is not None:
                migration_warnings.append(warning)
                logging.warning(
                    "[%s] %s；整改说明：%s",
                    warning["id"],
                    warning["summary"],
                    warning["remediation"],
                )
    pipeline = _build_sign_pipeline(
        args,
        hap_path,
        bundle_name,
        state_dir=state_dir,
        work_dir=work_dir,
        output_dir=output_dir,
        install_after_sign=deploy,
    )
    if not pipeline.run():
        message = getattr(pipeline, "last_error", "") or (
            "流程未完成；请查看 stderr 日志"
        )
        _failure(args.command, message, args.json_output)
        return EXIT_OPERATION_FAILED

    signed_hap = str(Path(pipeline.signed_hap_path).resolve())
    requested_capability_mode = None
    capability_mode = None
    capability_fallback = False
    if not input_signed:
        requested_capability_mode = (
            "system-basic" if args.enable_capability else "normal"
        )
        capability_mode = "system-basic" if pipeline.enable_capability else "normal"
        capability_fallback = requested_capability_mode != capability_mode
    payload: dict[str, object] = {
        "ok": True,
        "command": args.command,
        "message": "签名并安装成功" if deploy else "签名成功",
        "bundle_name": bundle_name,
        "serial": args.serial or "",
        "input_hap": str(hap_path),
        "input_signed": input_signed,
        "browser_mode": args.browser,
        "requested_capability_mode": requested_capability_mode,
        "capability_mode": capability_mode,
        "capability_fallback": capability_fallback,
        "migration_warnings": migration_warnings,
        "paths": {
            "state_dir": str(state_dir),
            "work_dir": str(work_dir),
            "output_dir": str(output_dir),
        },
        "signed_hap": signed_hap,
        "installed": deploy,
    }
    if deploy:
        bundle = _inspect_installed_bundle(args.serial, bundle_name)
        payload.update(bundle)
    _emit(payload, args.json_output)
    return EXIT_OK


def _run_install(args: argparse.Namespace) -> int:
    hap_path = _resolve_hap(args.hap)
    if not is_hap_signed(hap_path):
        raise ValueError("install 只接受已签名 HAP；请先使用 sign 或直接使用 deploy")
    bundle_name = args.bundle_name or detect_bundle_name(str(hap_path))
    with Installer(serial=args.serial) as installer:
        installer.install(str(hap_path))
        bundle = installer.inspect_bundle(bundle_name)
    if bundle is None:
        raise RuntimeError("安装命令结束后 bm dump 未确认目标 bundle")
    payload: dict[str, object] = {
        "ok": True,
        "command": "install",
        "message": "安装成功",
        "bundle_name": bundle_name,
        "serial": args.serial,
        "signed_hap": str(hap_path),
        "installed": True,
    }
    payload.update(bundle)
    _emit(payload, args.json_output)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """运行 CLI 并返回稳定退出码；JSON 模式永不输出凭据。"""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    parsed = _parse_args(parser, raw_args)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    _configure_logging(args.verbose)

    try:
        if args.command == "doctor":
            return _run_doctor(args)
        if args.command == "inspect":
            return _run_inspect(args)
        if args.command == "migrate-cache":
            return _run_migrate_cache(args)
        if args.command == "auth":
            return _run_auth(args)
        if args.command == "devices":
            return _run_devices(args)
        if args.command in {"sign", "deploy"}:
            return _run_sign_or_deploy(args)
        if args.command == "install":
            return _run_install(args)
        raise ValueError(f"未知命令: {args.command}")
    except OperationCancelled:
        _failure(args.command, "操作已取消", args.json_output, error_type="cancelled")
        return EXIT_CANCELLED
    except (ValueError, zipfile.BadZipFile) as exc:
        message = str(redact_sensitive_text(exc))
        _failure(args.command, message, args.json_output, error_type="invalid_input")
        return EXIT_USAGE
    except Exception as exc:
        message = str(redact_sensitive_text(exc))
        logging.getLogger(__name__).error("CLI 操作失败: %s", message)
        _failure(args.command, message, args.json_output)
        return EXIT_OPERATION_FAILED
