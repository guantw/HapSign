"""hapsign 命令行入口。"""

import argparse
import json
import logging
import zipfile
from collections.abc import Sequence

from hapsign import __version__
from hapsign.config import DEVICE_TYPE_PHONE
from hapsign.pipeline import SignPipeline


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


def build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="华为账号自动签名 + HAP 安装工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
设备类型码:
  4  手机 (默认)
  2  穿戴设备
  8  智慧屏
  9  路由器
  1  轻量级穿戴设备

示例:
  hapsign --hap app.hap
  hapsign --hap app.hap --bundle-name com.example.myapp
""",
    )
    parser.add_argument("--hap", required=True, help="未签名的 HAP 文件路径")
    parser.add_argument(
        "--bundle-name",
        default=None,
        help="应用包名（不传则从 HAP 内自动提取）",
    )
    parser.add_argument("--country", default="CN", help="国家码（默认 CN）")
    parser.add_argument(
        "--device-type",
        default=DEVICE_TYPE_PHONE,
        help="设备类型码（默认 4=手机，详见帮助底部）",
    )
    parser.add_argument(
        "--work-dir",
        default="",
        help="工作目录（存储签名材料，默认 signing_files/{bundle_name}/）",
    )
    parser.add_argument(
        "--enable-capability",
        action="store_true",
        help=(
            "使用 Real Profile（APL=system_basic），用于需要高权限的应用。"
            "需要应用已在 AGC 注册；未注册时自动回退到普通 Test Profile。"
        ),
    )
    parser.add_argument(
        "--refresh-token",
        action="store_true",
        help="强制重新登录，并连带刷新签名文件缓存。",
    )
    parser.add_argument(
        "--refresh-signing",
        action="store_true",
        help="强制重新申请证书、设备和 Profile，不重新登录。",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="显示调试日志")
    parser.add_argument(
        "--version",
        action="version",
        version=f"hapsign {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """运行命令行程序并返回进程退出码。"""
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    bundle_name = args.bundle_name or detect_bundle_name(args.hap)
    if not args.bundle_name:
        logging.info("自动检测到包名: %s", bundle_name)

    pipeline = SignPipeline(
        hap_path=args.hap,
        bundle_name=bundle_name,
        country=args.country,
        device_type=args.device_type,
        work_dir=args.work_dir,
        enable_capability=args.enable_capability,
        force_refresh_token=args.refresh_token,
        force_refresh_signing=args.refresh_signing,
    )
    return 0 if pipeline.run() else 1
