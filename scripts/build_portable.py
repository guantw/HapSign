"""构建当前平台的 HapSign 便携版目录和 ZIP。

构建机需要 Python、项目 bundle 依赖以及 prepare_toolchain 生成的公开工具链。
DevEco 仅作为显式启用的本机兼容回退。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = PROJECT_ROOT / "dist"
PREPARED_TOOLCHAIN_ROOT = PROJECT_ROOT / "build" / "toolchain-prepared"
# 实际冻结进便携包、需要随包附许可的 Python 运行时依赖。
# 门禁 _validate_runtime_license_coverage 会校验：本清单必须 ⊇ 实际冻结闭包。
PYTHON_RUNTIME_DISTRIBUTIONS = (
    "PySide6-Essentials",
    "shiboken6",
    "playwright",
    "greenlet",  # playwright 的运行时传递依赖
    "pyee",  # playwright 的运行时传递依赖
    "requests",
    "urllib3",
    "certifi",
    "charset-normalizer",
    "idna",
    "PyInstaller",
)
# 仅构建期使用的工具：其 bootloader 嵌入冻结产物故保留许可记录，但自身与其传递
# 依赖并不随便携包分发，因此不计入门禁的“实际冻结闭包”。
BUILD_TIME_ONLY_DISTRIBUTIONS = ("PyInstaller",)


def _write_sha256_file(archive_path: Path) -> Path:
    """为发布归档生成可被常见校验工具读取的 SHA-256 sidecar。"""

    digest = hashlib.sha256()
    with archive_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{digest.hexdigest()}  {archive_path.name}\n",
        encoding="ascii",
    )
    return checksum_path


# 以下排除项只用于显式启用的 DevEco JBR 回退；正式构建使用 jlink runtime。
# 这里仅排除能够由构建后工具链自检证明无关的 JCEF 原生资源；Java modules、
# 字体、安全配置和其他 DLL 全部保留，后续精简需单独增加测试后再进行。
WINDOWS_JCEF_BIN_FILES = {
    "chrome_elf.dll",
    "d3dcompiler_47.dll",
    "dxcompiler.dll",
    "dxil.dll",
    "icudtl.dat",
    "jcef.dll",
    "jcef_helper.dll",
    "jcef_helper.exe",
    "jogl_desktop.dll",
    "jogl_mobile.dll",
    "libcef.dll",
    "libegl.dll",
    "libglesv2.dll",
    "snapshot_blob.bin",
    "v8_context_snapshot.bin",
    "vk_swiftshader.dll",
    "vulkan-1.dll",
}
WINDOWS_JCEF_LIB_FILES = {
    "chrome_100_percent.pak",
    "chrome_200_percent.pak",
    "resources.pak",
    "vk_swiftshader_icd.json",
}
WINDOWS_JCEF_LIB_DIRS = {"locales"}


def _require_build_modules() -> None:
    missing = [
        name
        for name in ("PyInstaller", "PySide6", "playwright")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        packages = " ".join(missing)
        raise RuntimeError(
            "缺少构建依赖："
            f"{', '.join(missing)}\n"
            f"请先执行：{sys.executable} -m pip install -e .[gui,bundle]\n"
            f"检测名称：{packages}"
        )


def _require_playwright_browser() -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
    if executable.is_file():
        return
    raise RuntimeError(
        "缺少 Playwright Chromium 浏览器。\n"
        "请在 PowerShell 中执行：\n"
        '$env:PLAYWRIGHT_BROWSERS_PATH="0"\n'
        f"{sys.executable} -m playwright install --no-shell chromium"
    )


def _run(command: list[str], *, env: dict[str, str]) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _runtime_requirement_names(distribution_name: str) -> set[str]:
    """返回 distribution 在当前环境生效的核心运行时依赖名（排除 extras 条件依赖）。

    `metadata.requires` 返回的字符串形如 `greenlet>=3.1.1 ; python_version >= '3.9'`
    或 `pyee (>=12.0.0) ; extra == 'gui'`；这里只解析名字部分，并丢弃带
    `extra ==` 标记的条件依赖。
    """
    names: set[str] = set()
    try:
        raw_requirements = metadata.requires(distribution_name)
    except metadata.PackageNotFoundError:
        return names
    for raw in raw_requirements or ():
        if "extra ==" in raw:
            continue
        name_part = raw.split(";", 1)[0].strip()
        if not name_part:
            continue
        # 只取名字部分：截断到版本比较符、逗号或空白。
        # 例如 "greenlet>=3.1.1" → "greenlet"；"pyee (>=12.0.0)" → "pyee"。
        name = re.split(r"[<>=!~,\s]+", name_part, maxsplit=1)[0].strip()
        if name:
            names.add(name)
    return names


def _normalize_distribution_name(name: str) -> str:
    """PEP 503 规范化：小写并把 `-`/`_`/`.` 合并为 `-`。

    等价于 importlib.metadata.normalize_name（Python 3.13+ 才公开），
    3.11/3.12 构建机这里自行实现。
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _frozen_runtime_closure(roots: tuple[str, ...]) -> set[str]:
    """从实际冻结的根依赖出发，解析传递的运行时依赖闭包（规范化名称）。

    只跟随当前环境实际生效的核心运行时依赖；构建期工具不在 roots 中，
    其依赖也不会被误收。
    """
    closure: set[str] = set()
    pending = [_normalize_distribution_name(root) for root in roots]
    while pending:
        current = pending.pop()
        if current in closure:
            continue
        closure.add(current)
        for dep in _runtime_requirement_names(current):
            normalized = _normalize_distribution_name(dep)
            if normalized not in closure:
                pending.append(normalized)
    return closure


def _validate_runtime_license_coverage() -> None:
    """门禁：许可清单必须覆盖实际冻结闭包，缺项直接让构建失败。

    断言方向为「清单 ⊇ 闭包」：只允许闭包比清单多而报错，不允许按清单反向
    裁剪实际冻结内容，否则构建机与发布环境不一致时差距会被悄悄掩盖。
    """
    roots = tuple(
        name
        for name in PYTHON_RUNTIME_DISTRIBUTIONS
        if name not in BUILD_TIME_ONLY_DISTRIBUTIONS
    )
    listed = {
        _normalize_distribution_name(name) for name in PYTHON_RUNTIME_DISTRIBUTIONS
    }
    closure = _frozen_runtime_closure(roots)
    missing = sorted(name for name in closure if name not in listed)
    if missing:
        raise RuntimeError(
            "第三方许可清单缺少实际冻结的运行时依赖："
            f"{', '.join(missing)}\n"
            "请在 PYTHON_RUNTIME_DISTRIBUTIONS 中补全后再构建。"
        )


def _copy_python_license_files(portable_root: Path) -> None:
    """复制冻结依赖随 wheel 安装的许可，并记录精确构建版本。"""
    license_root = portable_root / "licenses" / "python"
    license_root.mkdir(parents=True, exist_ok=True)
    manifest = [
        "Python distributions frozen into this build",
        "===========================================",
        "",
        "Generated from the active build environment.",
        "",
    ]

    for distribution_name in PYTHON_RUNTIME_DISTRIBUTIONS:
        try:
            distribution = metadata.distribution(distribution_name)
        except metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                f"无法生成第三方许可清单，缺少 distribution: {distribution_name}"
            ) from exc

        canonical_name = distribution.metadata.get("Name", distribution_name)
        version = distribution.version
        license_expression = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or "(not declared in package metadata)"
        )
        manifest.append(f"- {canonical_name} {version}: {license_expression}")

        destination = license_root / f"{canonical_name}-{version}"
        copied = 0
        for entry in distribution.files or ():
            normalized = str(entry).replace("\\", "/")
            lowered = normalized.lower()
            filename = Path(normalized).name.lower()
            is_license_name = filename.startswith(
                ("license", "copying", "notice", "thirdpartynotice")
            )
            if ".dist-info/licenses/" not in lowered and not (
                ".dist-info/" in lowered and is_license_name
            ):
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            target = destination / Path(normalized).name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1

        if copied == 0:
            manifest.append(
                f"  No standalone license file was present in the installed "
                f"{canonical_name} distribution; see THIRD_PARTY_NOTICES.md."
            )

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError(f"无法找到 Python 运行时许可文件: {python_license}")
    shutil.copy2(python_license, license_root / "PYTHON-LICENSE.txt")

    (license_root / "DISTRIBUTIONS.txt").write_text(
        "\n".join(manifest) + "\n",
        encoding="utf-8",
    )


def _copy_release_documents(portable_root: Path) -> None:
    """把源码许可、隐私和构建说明放到可执行包根目录。"""
    documents = {
        "PORTABLE.md": "README.md",
        "LICENSE": "LICENSE",
        "PRIVACY.md": "PRIVACY.md",
        "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
        "docs/PACKAGING.md": "BUILDING.md",
        "docs/OPEN_SOURCE_RELEASE.md": "OPEN_SOURCE_RELEASE.md",
    }
    for source_name, target_name in documents.items():
        shutil.copy2(PROJECT_ROOT / source_name, portable_root / target_name)
    _copy_python_license_files(portable_root)


def _jbr_ignore(source_root: Path):
    bin_dir = (source_root / "bin").resolve()
    lib_dir = (source_root / "lib").resolve()

    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory).resolve()
        lowered = {name.lower(): name for name in names}
        if current == bin_dir:
            return {lowered[name] for name in WINDOWS_JCEF_BIN_FILES if name in lowered}
        if current == lib_dir:
            excluded = WINDOWS_JCEF_LIB_FILES | WINDOWS_JCEF_LIB_DIRS
            return {lowered[name] for name in excluded if name in lowered}
        return set()

    return ignore


def _smoke_test_toolchain(portable_root: Path) -> None:
    from hapsign.runtime import platform_tag

    root = portable_root / "resources" / "toolchain" / platform_tag()
    suffix = ".exe" if sys.platform == "win32" else ""
    runtime = root / "runtime"
    if not runtime.is_dir():
        runtime = root / "jbr"
    java = runtime / "bin" / f"java{suffix}"
    keytool = runtime / "bin" / f"keytool{suffix}"
    signer = root / "lib" / "hap-sign-tool.jar"

    checks = [
        [str(java), "-version"],
        [str(java), "-jar", str(signer)],
    ]
    with tempfile.TemporaryDirectory(prefix="hapsign-toolchain-smoke-") as temp:
        keystore = Path(temp) / "smoke.p12"
        checks.append(
            [
                str(keytool),
                "-genkeypair",
                "-alias",
                "hapsign-smoke",
                "-keyalg",
                "EC",
                "-groupname",
                "secp256r1",
                "-keystore",
                str(keystore),
                "-storetype",
                "PKCS12",
                "-storepass",
                "hapsign-smoke-password",
                "-keypass",
                "hapsign-smoke-password",
                "-dname",
                "CN=HapSign Build Smoke",
                "-validity",
                "1",
                "-noprompt",
            ]
        )
        for command in checks:
            result = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                details = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    "精简后的便携 Java 工具链自检失败："
                    f"{subprocess.list2cmdline(command)}\n{details}"
                )


def _copy_local_toolchain(portable_root: Path, *, keep_full_jbr: bool) -> None:
    """从本机安装复制工具，仅供兼容和排障，不用于公开 Release。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    from hapsign.runtime import discover_toolchain, platform_tag

    toolchain = discover_toolchain()
    missing = toolchain.missing()
    if missing:
        details = "\n".join(f"- {item}" for item in missing)
        raise RuntimeError(f"无法生成完整便携版，工具链缺失：\n{details}")

    jbr_root = toolchain.java.parent.parent
    target = portable_root / "resources" / "toolchain" / platform_tag()
    target.mkdir(parents=True, exist_ok=True)
    copy_options = {"dirs_exist_ok": True}
    if sys.platform == "win32" and not keep_full_jbr:
        copy_options["ignore"] = _jbr_ignore(jbr_root)
    shutil.copytree(jbr_root, target / "jbr", **copy_options)

    lib_dir = target / "lib"
    bin_dir = target / "bin"
    lib_dir.mkdir(exist_ok=True)
    bin_dir.mkdir(exist_ok=True)
    shutil.copy2(toolchain.hap_sign_tool, lib_dir / "hap-sign-tool.jar")
    shutil.copy2(toolchain.hdc, bin_dir / toolchain.hdc.name)

    # Windows HDC 与同目录的 libusb_shared.dll 配套分发。
    libusb = toolchain.hdc.parent / "libusb_shared.dll"
    if libusb.is_file():
        shutil.copy2(libusb, bin_dir / libusb.name)
    notice = toolchain.hdc.parent / "NOTICE.txt"
    if notice.is_file():
        shutil.copy2(notice, target / "NOTICE.txt")

    # DevEco 自身的综合 NOTICE 可补充 JBR/SDK 安装包中的许可文本。它不能代替
    # 发布者核对具体版本的再分发权，但公开二进制不得把随附声明裁掉。
    deveco_notice = jbr_root.parent / "license" / "NOTICE.txt"
    if deveco_notice.is_file():
        notice_target = portable_root / "licenses" / "deveco-studio"
        notice_target.mkdir(parents=True, exist_ok=True)
        shutil.copy2(deveco_notice, notice_target / "NOTICE.txt")

    manifest_files = {
        "java": target / "jbr" / "bin" / toolchain.java.name,
        "keytool": target / "jbr" / "bin" / toolchain.keytool.name,
        "hap-sign-tool": target / "lib" / "hap-sign-tool.jar",
        "hdc": target / "bin" / toolchain.hdc.name,
    }
    manifest_lines = [
        "Bundled toolchain provenance",
        "============================",
        "",
        f"Detected source type: {toolchain.source}",
        "The source installation path is intentionally omitted for privacy.",
        "A local build does not by itself grant public redistribution rights.",
        "",
    ]
    for name, path in manifest_files.items():
        manifest_lines.append(
            f"{name}: {path.relative_to(portable_root).as_posix()} "
            f"sha256={_sha256(path)}"
        )
    (target / "PROVENANCE.txt").write_text(
        "\n".join(manifest_lines) + "\n",
        encoding="utf-8",
    )
    _smoke_test_toolchain(portable_root)


def _copy_toolchain(
    portable_root: Path,
    *,
    keep_full_jbr: bool,
    allow_local_toolchain: bool,
) -> None:
    """优先复制已锁定的公开工具链；本机 DevEco 回退必须显式启用。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    from hapsign.runtime import platform_tag

    prepared = PREPARED_TOOLCHAIN_ROOT / platform_tag()
    target = portable_root / "resources" / "toolchain" / platform_tag()
    required = (
        prepared / "PROVENANCE.txt",
        prepared / "toolchain.lock.json",
        prepared / "NOTICE.txt",
    )
    if prepared.is_dir() and all(path.is_file() for path in required):
        shutil.copytree(prepared, target, dirs_exist_ok=True)
        _smoke_test_toolchain(portable_root)
        return

    if not allow_local_toolchain:
        raise RuntimeError(
            "尚未准备可公开分发的锁定工具链。\n"
            f"请先执行：{sys.executable} scripts/prepare_toolchain.py\n"
            "仅为本机排障时可显式传入 --allow-deveco-toolchain；"
            "该回退产物不应上传公开 Release。"
        )
    _copy_local_toolchain(portable_root, keep_full_jbr=keep_full_jbr)


def _prune_playwright_extras(
    portable_root: Path,
    *,
    keep_bundled_browser: bool,
) -> None:
    """按构建模式清理不需要的 Playwright 浏览器资源。"""
    internal = portable_root / "_internal"
    browsers = internal.glob("playwright/**/.local-browsers/*")
    for browser in browsers:
        if not browser.is_dir():
            continue
        if browser.name.startswith("ffmpeg-") or (
            browser.name.startswith("chromium-") and not keep_bundled_browser
        ):
            shutil.rmtree(browser)


def _smoke_test_frozen_app(
    portable_root: Path,
    *,
    keep_bundled_browser: bool,
) -> None:
    executable_name = "HapSign.exe" if sys.platform == "win32" else "HapSign"
    executable = portable_root / executable_name
    checks = ["--system-browser-smoke-test", "--smoke-test"]
    if keep_bundled_browser:
        checks.insert(1, "--browser-smoke-test")
    for argument in checks:
        result = subprocess.run(
            [str(executable), argument],
            cwd=portable_root,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"冻结应用自检失败：{executable.name} {argument} "
                f"(code={result.returncode})"
            )


def _remove_smoke_artifacts(portable_root: Path) -> None:
    """避免把构建自检生成的本地日志或运行数据收入发布包。"""
    config = portable_root / "hapsign-config.json"
    if config.is_file():
        config.unlink()
    for name in ("logs", "signing_files", "signed_haps"):
        directory = portable_root / name
        if directory.is_dir():
            shutil.rmtree(directory)


def build(
    *,
    skip_toolchain: bool,
    keep_full_jbr: bool,
    keep_bundled_browser: bool,
    allow_local_toolchain: bool,
) -> Path:
    _require_build_modules()
    _validate_runtime_license_coverage()
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
    if keep_bundled_browser:
        _require_playwright_browser()
    env = os.environ.copy()
    env["HAPSIGN_BUNDLE_CHROMIUM"] = "1" if keep_bundled_browser else "0"

    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(PROJECT_ROOT / "build" / "pyinstaller"),
            str(PROJECT_ROOT / "bundle" / "hapsign.spec"),
        ],
        env=env,
    )

    portable_root = DIST_DIR / "HapSign"
    _prune_playwright_extras(
        portable_root,
        keep_bundled_browser=keep_bundled_browser,
    )
    if not skip_toolchain:
        _copy_toolchain(
            portable_root,
            keep_full_jbr=keep_full_jbr,
            allow_local_toolchain=allow_local_toolchain,
        )
    _copy_release_documents(portable_root)
    _smoke_test_frozen_app(
        portable_root,
        keep_bundled_browser=keep_bundled_browser,
    )
    _remove_smoke_artifacts(portable_root)

    sys.path.insert(0, str(PROJECT_ROOT))
    from hapsign.runtime import platform_tag

    archive_variant = "-compat" if keep_bundled_browser else ""
    archive_base = DIST_DIR / (f"HapSign-portable-{platform_tag()}{archive_variant}")
    archive_path = archive_base.with_suffix(".zip")
    if archive_path.is_file():
        archive_path.unlink()
    shutil.make_archive(
        str(archive_base),
        "zip",
        root_dir=portable_root.parent,
        base_dir=portable_root.name,
    )
    _write_sha256_file(archive_path)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-toolchain",
        action="store_true",
        help="只构建 GUI，不复制签名与设备工具链",
    )
    parser.add_argument(
        "--keep-full-jbr",
        action="store_true",
        help="使用 DevEco 回退时保留完整 JBR 的 JCEF 资源",
    )
    parser.add_argument(
        "--allow-deveco-toolchain",
        action="store_true",
        help="公开工具链未准备好时允许复制本机 DevEco 工具；产物不可直接公开发布",
    )
    parser.add_argument(
        "--keep-bundled-browser",
        action="store_true",
        help="额外包含 Playwright Chromium；默认复用系统 Edge/Chrome 以缩小体积",
    )
    args = parser.parse_args()

    try:
        archive = build(
            skip_toolchain=args.skip_toolchain,
            keep_full_jbr=args.keep_full_jbr,
            keep_bundled_browser=args.keep_bundled_browser,
            allow_local_toolchain=args.allow_deveco_toolchain,
        )
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        return 1
    print(f"便携版已生成：{archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
