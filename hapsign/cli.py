"""Agent 友好的 HapSign 命令行入口。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from hapsign import __version__
from hapsign.cancellation import OperationCancelled
from hapsign.config import DEVICE_TYPE_PHONE
from hapsign.diagnostics import redact_sensitive_text
from hapsign.pipeline import SignPipeline, default_state_dir
from hapsign.signing.hap_inspect import is_hap_signed
from hapsign.signing.installer import Installer
from hapsign.token import secure_token_cache

COMMANDS = {"auth", "devices", "sign", "install", "deploy"}
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
        default=default_state_dir(),
        help=(
            "Token 与默认签名材料根目录；默认用户主目录 ~/.hapsign"
            "（Windows 为 %%USERPROFILE%%\\.hapsign）。"
            "Token 不会出现在 JSON 输出中"
        ),
    )


def _add_hap_identity_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--hap", required=True, help="HAP 文件的绝对或相对路径")
    parser.add_argument(
        "--bundle-name",
        default=None,
        help="覆盖 module.json 中的 bundleName；通常不需要指定",
    )


def _add_signing_options(parser: argparse.ArgumentParser) -> None:
    _add_hap_identity_options(parser)
    parser.add_argument(
        "--serial",
        required=True,
        help="hdc list targets 返回的目标序列号；Profile 将绑定该设备",
    )
    parser.add_argument("--country", default="CN", help="华为账号国家码；默认 CN")
    parser.add_argument(
        "--device-type",
        default=DEVICE_TYPE_PHONE,
        help="签名平台注册的设备类型码；默认 4（手机/平板/2in1）",
    )
    _add_state_option(parser)
    parser.add_argument(
        "--work-dir",
        default="",
        help="当前 bundle 的签名材料目录；默认 <state-dir>/<bundle>",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="签名 HAP 输出目录；默认与当前 bundle 的签名材料目录相同",
    )
    parser.add_argument(
        "--browser",
        choices=("system", "system_controlled", "playwright"),
        default="system",
        help="首次认证使用的浏览器模式；CLI 默认 system",
    )
    parser.add_argument(
        "--enable-capability",
        action="store_true",
        help="尝试使用 Real Profile（APL=system_basic）；需要已注册 AGC 应用",
    )
    parser.add_argument(
        "--refresh-token",
        action="store_true",
        help="删除当日 Token 缓存并重新浏览器认证，同时刷新签名材料",
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
  hapsign devices list --connected-only --json
  hapsign auth status --json
  hapsign auth --json
  hapsign sign --hap app-unsigned.hap --serial <serial> --json
  hapsign install --hap app-signed.hap --serial <serial> --json
  hapsign deploy --hap app-unsigned.hap --serial <serial> --json

兼容旧调用:
  hapsign --hap app.hap --serial <serial>    等价于 hapsign deploy ...

退出码:
  0    成功
  1    运行失败（认证、签名、HDC 或安装失败）
  2    参数或输入文件无效
  130  用户取消
""",
    )
    parser.add_argument("--version", action="version", version=f"hapsign {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

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
  hapsign auth --json                 # 有当日缓存则复用，否则打开浏览器
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
        choices=("system", "system_controlled", "playwright"),
        default="system",
        help="认证浏览器模式；CLI 默认 system",
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
        help="为指定设备签名 HAP，但不安装",
        description=(
            "为 --serial 对应设备生成 debug Profile 并签名。"
            "已有当日 Auth Token 会复用；"
            "同一 bundle 切换设备时会自动丢弃不匹配的 Profile 缓存。"
        ),
        formatter_class=_formatter,
        epilog="""\
示例:
  hapsign sign --hap app-unsigned.hap --serial <serial> --json
  hapsign sign --hap app.hap --serial <serial> --output-dir ./signed --json
""",
    )
    _add_signing_options(sign)

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
    install.add_argument("--serial", required=True, help="目标 HDC 序列号")
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
    _add_signing_options(deploy)
    return parser


def _normalize_legacy_args(argv: Sequence[str]) -> list[str]:
    """把旧 ``hapsign --hap ...`` 调用转换成 ``deploy`` 子命令。"""
    normalized = list(argv)
    if "--hap" in normalized and not any(item in COMMANDS for item in normalized[:1]):
        normalized.insert(0, "deploy")
    return normalized


def _command_from_args(argv: Sequence[str]) -> str:
    return next((item for item in argv if item in COMMANDS), "unknown")


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
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
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
                    "发现当日认证缓存"
                    if status["authenticated"]
                    else "没有可用的当日认证缓存"
                ),
                "cache_format": _cache_format(str(status["cache_path"])),
            }
        )
        _emit(status, args.json_output)
        return EXIT_OK

    result = pipeline.authenticate(force_refresh=args.refresh)
    status = pipeline.auth_status()
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
    install_after_sign: bool,
) -> SignPipeline:
    return SignPipeline(
        hap_path=str(hap_path),
        bundle_name=bundle_name,
        country=args.country,
        device_type=args.device_type,
        serial=args.serial,
        work_dir=args.work_dir,
        state_dir=args.state_dir,
        enable_capability=args.enable_capability,
        force_refresh_token=args.refresh_token,
        force_refresh_signing=args.refresh_signing,
        browser_mode=args.browser,
        signed_output_dir=args.output_dir,
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
    hap_path = _resolve_hap(args.hap)
    bundle_name = args.bundle_name or detect_bundle_name(str(hap_path))
    input_signed = is_hap_signed(hap_path)
    deploy = args.command == "deploy"
    pipeline = _build_sign_pipeline(
        args,
        hap_path,
        bundle_name,
        install_after_sign=deploy,
    )
    if not pipeline.run():
        message = getattr(pipeline, "last_error", "") or (
            "流程未完成；请查看 stderr 日志"
        )
        _failure(args.command, message, args.json_output)
        return EXIT_OPERATION_FAILED

    signed_hap = str(Path(pipeline.signed_hap_path).resolve())
    payload: dict[str, object] = {
        "ok": True,
        "command": args.command,
        "message": "签名并安装成功" if deploy else "签名成功",
        "bundle_name": bundle_name,
        "serial": args.serial,
        "input_hap": str(hap_path),
        "input_signed": input_signed,
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
    normalized_args = _normalize_legacy_args(raw_args)
    parsed = _parse_args(parser, normalized_args)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    _configure_logging(args.verbose)

    try:
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
