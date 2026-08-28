"""从可审计的公开上游准备 HapSign 便携工具链。

下载内容、大小和 SHA-256 固定在 ``toolchain.lock.json``。Windows 当前使用：

* OpenHarmony 6.1 公共 SDK 中的 HDC、libusb 和 hap-sign-tool；
* Eclipse Temurin 21 JDK，经 jlink 缩成仅供 HapSign 使用的运行时。

缓存和输出默认都位于被 Git 忽略的 ``build/``，不会把大型 SDK 加进源码仓库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = PROJECT_ROOT / "toolchain.lock.json"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "build" / "toolchain-cache"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "build" / "toolchain-prepared"
DOWNLOAD_CHUNK_SIZE = 4 * 1024 * 1024


def _platform_tag() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def _verify_file(path: Path, metadata: dict[str, Any], *, label: str) -> None:
    expected_size = metadata.get("size")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise RuntimeError(
            f"{label} 大小校验失败：期望 {expected_size}，实际 {path.stat().st_size}，"
            f"文件：{path}"
        )
    expected_hash = metadata.get("sha256")
    if expected_hash is not None:
        actual_hash = _sha256(path)
        if actual_hash != expected_hash.lower():
            raise RuntimeError(
                f"{label} SHA-256 校验失败：\n"
                f"期望 {expected_hash.lower()}\n实际 {actual_hash}\n文件：{path}"
            )


def _download(
    metadata: dict[str, Any],
    destination: Path,
    *,
    label: str,
) -> Path:
    """下载并校验锁定文件；使用 .part 支持可靠的断点续传。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        _verify_file(destination, metadata, label=label)
        print(f"使用已校验缓存：{destination}")
        return destination

    partial = destination.with_name(f"{destination.name}.part")
    expected_size = int(metadata["size"])
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > expected_size:
        raise RuntimeError(
            f"下载缓存大于锁定文件，请删除后重试：{partial} "
            f"({offset} > {expected_size})"
        )

    headers = {"User-Agent": "HapSign-toolchain-preparer/1"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(metadata["url"], headers=headers)
    print(f"下载 {label}：{metadata['url']}")
    if offset:
        print(f"从 {offset} 字节继续")

    with urllib.request.urlopen(request, timeout=60) as response:
        status = getattr(response, "status", response.getcode())
        mode = "ab"
        if offset and status != 206:
            print("服务器未接受断点续传，改为从头下载")
            offset = 0
            mode = "wb"
        elif not offset:
            mode = "wb"

        downloaded = offset
        last_report = time.monotonic()
        with partial.open(mode) as target:
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_report >= 5:
                    percent = downloaded / expected_size * 100
                    print(
                        f"\r  {downloaded / 1024**2:.1f} MiB / "
                        f"{expected_size / 1024**2:.1f} MiB ({percent:.1f}%)",
                        end="",
                        flush=True,
                    )
                    last_report = now
    print()
    _verify_file(partial, metadata, label=label)
    os.replace(partial, destination)
    return destination


def _archive_name(url: str) -> str:
    name = PurePosixPath(urllib.parse.urlparse(url).path).name
    if not name:
        raise RuntimeError(f"下载地址没有文件名：{url}")
    return name


def _extract_nested_toolchains(
    sdk_archive: Path,
    *,
    expected_name: str,
    destination: Path,
) -> Path:
    """从公共 SDK tar.gz 中只提取目标平台 toolchains ZIP。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(sdk_archive, mode="r:gz") as archive:
        matches = [
            member
            for member in archive.getmembers()
            if member.isfile() and PurePosixPath(member.name).name == expected_name
        ]
        if len(matches) != 1:
            names = ", ".join(item.name for item in matches) or "(未找到)"
            raise RuntimeError(
                f"公共 SDK 中应当恰好有一个 {expected_name}，实际：{names}"
            )
        source = archive.extractfile(matches[0])
        if source is None:
            raise RuntimeError(f"无法读取公共 SDK 成员：{matches[0].name}")
        with source, destination.open("wb") as target:
            shutil.copyfileobj(source, target, length=DOWNLOAD_CHUNK_SIZE)
    return destination


def _normalized_zip_name(name: str) -> str:
    return str(PurePosixPath(name.replace("\\", "/")))


def _find_zip_member(archive: zipfile.ZipFile, suffix: str) -> zipfile.ZipInfo:
    normalized_suffix = _normalized_zip_name(suffix).lower()
    matches = [
        item
        for item in archive.infolist()
        if not item.is_dir()
        and _normalized_zip_name(item.filename).lower().endswith(normalized_suffix)
    ]
    if len(matches) != 1:
        names = ", ".join(item.filename for item in matches) or "(未找到)"
        raise RuntimeError(f"toolchains ZIP 中应当恰好有一个 *{suffix}，实际：{names}")
    return matches[0]


def _extract_openharmony_files(
    toolchains_zip: Path,
    output: Path,
    files: dict[str, dict[str, Any]],
) -> None:
    with zipfile.ZipFile(toolchains_zip) as archive:
        for target_name, metadata in files.items():
            member = _find_zip_member(archive, metadata["archive_suffix"])
            target = output / Path(target_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=DOWNLOAD_CHUNK_SIZE)
            _verify_file(target, metadata, label=target_name)


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (destination / _normalized_zip_name(member.filename)).resolve()
            if (
                target != destination_resolved
                and destination_resolved not in target.parents
            ):
                raise RuntimeError(f"ZIP 包含越界路径：{member.filename}")
        archive.extractall(destination)


def _find_jdk_root(extracted: Path) -> Path:
    executable = "jlink.exe" if os.name == "nt" else "jlink"
    matches = list(extracted.glob(f"*/bin/{executable}"))
    if len(matches) != 1:
        names = ", ".join(str(item) for item in matches) or "(未找到)"
        raise RuntimeError(f"Temurin ZIP 中无法唯一定位 {executable}：{names}")
    return matches[0].parent.parent


def _create_java_runtime(
    jdk_archive: Path,
    output: Path,
    *,
    modules: list[str],
) -> None:
    with tempfile.TemporaryDirectory(prefix="hapsign-temurin-") as temporary:
        extracted = Path(temporary)
        _safe_extract_zip(jdk_archive, extracted)
        jdk_root = _find_jdk_root(extracted)
        executable = "jlink.exe" if os.name == "nt" else "jlink"
        command = [
            str(jdk_root / "bin" / executable),
            "--add-modules",
            ",".join(modules),
            "--strip-debug",
            "--no-man-pages",
            "--no-header-files",
            "--compress=zip-6",
            "--output",
            str(output / "runtime"),
        ]
        print("+", subprocess.list2cmdline(command))
        subprocess.run(command, check=True)

        license_dir = output / "licenses" / "temurin"
        license_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "NOTICE",
            "LICENSE",
            "ADDITIONAL_LICENSE_INFO",
            "ASSEMBLY_EXCEPTION",
        ):
            source = jdk_root / name
            if source.is_file():
                shutil.copy2(source, license_dir / name)


def _copy_libusb_source(output: Path, metadata: dict[str, Any]) -> None:
    source = PROJECT_ROOT / metadata["path"]
    _verify_file(source, metadata, label="libusb 对应源代码")
    destination = output / "licenses" / "libusb-source"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination / source.name)
    readme = source.parent / "README.md"
    if readme.is_file():
        shutil.copy2(readme, destination / "README.md")


def _smoke_test(output: Path) -> None:
    suffix = ".exe" if os.name == "nt" else ""
    java = output / "runtime" / "bin" / f"java{suffix}"
    keytool = output / "runtime" / "bin" / f"keytool{suffix}"
    signer = output / "lib" / "hap-sign-tool.jar"
    hdc = output / "bin" / f"hdc{suffix}"

    commands = [
        [str(java), "-version"],
        [str(java), "-jar", str(signer)],
        [str(hdc), "-v"],
    ]
    with tempfile.TemporaryDirectory(prefix="hapsign-toolchain-smoke-") as temporary:
        keystore = Path(temporary) / "smoke.p12"
        commands.append(
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
                "CN=HapSign Toolchain Smoke",
                "-validity",
                "1",
                "-noprompt",
            ]
        )
        for command in commands:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                details = result.stderr.strip() or result.stdout.strip()
                raise RuntimeError(
                    f"工具链自检失败：{subprocess.list2cmdline(command)}\n{details}"
                )


def _write_provenance(
    output: Path,
    *,
    platform_config: dict[str, Any],
) -> None:
    openharmony = platform_config["openharmony"]
    java = platform_config["java"]
    files = {
        "java": output
        / "runtime"
        / "bin"
        / ("java.exe" if os.name == "nt" else "java"),
        "keytool": output
        / "runtime"
        / "bin"
        / ("keytool.exe" if os.name == "nt" else "keytool"),
        "hap-sign-tool": output / "lib" / "hap-sign-tool.jar",
        "hdc": output / "bin" / ("hdc.exe" if os.name == "nt" else "hdc"),
    }
    lines = [
        "HapSign verified public toolchain",
        "=================================",
        "",
        f"OpenHarmony SDK: {openharmony['release']} / {openharmony['version']}",
        f"OpenHarmony archive: {openharmony['archive']['url']}",
        f"OpenHarmony archive SHA-256: {openharmony['archive']['sha256']}",
        f"Java: {java['distribution']} {java['version']}",
        f"Java archive: {java['archive']['url']}",
        f"Java archive SHA-256: {java['archive']['sha256']}",
        "Java runtime: generated with jlink from the locked JDK archive",
        "",
        "Bundled files:",
    ]
    for name, path in files.items():
        lines.append(
            f"- {name}: {path.relative_to(output).as_posix()} sha256={_sha256(path)}"
        )
    (output / "PROVENANCE.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(LOCK_PATH, output / "toolchain.lock.json")


def prepare(
    *,
    target_platform: str,
    cache_dir: Path,
    output: Path,
    sdk_archive_override: Path | None,
    jdk_archive_override: Path | None,
    force: bool,
) -> Path:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("schema") != 1:
        raise RuntimeError(f"不支持的工具链锁文件版本：{lock.get('schema')}")
    try:
        platform_config = lock["platforms"][target_platform]
    except KeyError as exc:
        supported = ", ".join(sorted(lock["platforms"]))
        raise RuntimeError(
            f"尚未锁定 {target_platform} 工具链；当前支持：{supported}"
        ) from exc
    if target_platform != _platform_tag():
        raise RuntimeError(
            f"jlink 不能跨平台生成运行时：目标 {target_platform}，"
            f"当前主机 {_platform_tag()}"
        )

    if output.exists() and not force:
        raise RuntimeError(f"输出目录已存在；确认后使用 --force 重建：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    openharmony = platform_config["openharmony"]
    java = platform_config["java"]
    sdk_archive = sdk_archive_override
    if sdk_archive is None:
        sdk_archive = cache_dir / _archive_name(openharmony["archive"]["url"])
        sdk_archive = _download(
            openharmony["archive"],
            sdk_archive,
            label="OpenHarmony 公共 SDK",
        )
    else:
        sdk_archive = sdk_archive.resolve()
        _verify_file(sdk_archive, openharmony["archive"], label="OpenHarmony 公共 SDK")

    jdk_archive = jdk_archive_override
    if jdk_archive is None:
        jdk_archive = cache_dir / _archive_name(java["archive"]["url"])
        jdk_archive = _download(
            java["archive"], jdk_archive, label="Eclipse Temurin JDK"
        )
    else:
        jdk_archive = jdk_archive.resolve()
        _verify_file(jdk_archive, java["archive"], label="Eclipse Temurin JDK")

    cache_dir.mkdir(parents=True, exist_ok=True)
    toolchains_zip = cache_dir / openharmony["toolchains_member"]
    if not toolchains_zip.is_file():
        print(f"从公共 SDK 提取：{openharmony['toolchains_member']}")
        _extract_nested_toolchains(
            sdk_archive,
            expected_name=openharmony["toolchains_member"],
            destination=toolchains_zip,
        )
    _verify_file(
        toolchains_zip,
        {"sha256": openharmony["toolchains_sha256"]},
        label="OpenHarmony Windows toolchains",
    )

    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent)))
    try:
        _extract_openharmony_files(
            toolchains_zip,
            staging,
            openharmony["files"],
        )
        _create_java_runtime(
            jdk_archive,
            staging,
            modules=java["modules"],
        )
        _copy_libusb_source(staging, openharmony["libusb_source"])
        _write_provenance(staging, platform_config=platform_config)
        _smoke_test(staging)
        if output.exists():
            shutil.rmtree(output)
        staging.replace(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"已生成并验证公开工具链：{output}")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--platform",
        default=_platform_tag(),
        choices=("windows", "macos", "linux"),
        help="目标平台；jlink 要求与当前主机一致",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=DEFAULT_CACHE_DIR,
        help="下载缓存目录",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；默认 build/toolchain-prepared/<platform>",
    )
    parser.add_argument(
        "--sdk-archive",
        type=Path,
        help="使用已下载的 OpenHarmony 公共 SDK，同时仍执行锁定校验",
    )
    parser.add_argument(
        "--jdk-archive",
        type=Path,
        help="使用已下载的 Temurin JDK ZIP，同时仍执行锁定校验",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="删除并重建已存在的目标工具链目录",
    )
    args = parser.parse_args()
    output = args.output_dir or DEFAULT_OUTPUT_ROOT / args.platform
    try:
        prepare(
            target_platform=args.platform,
            cache_dir=args.cache_dir.resolve(),
            output=output.resolve(),
            sdk_archive_override=args.sdk_archive,
            jdk_archive_override=args.jdk_archive,
            force=args.force,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"准备工具链失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
