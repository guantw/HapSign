"""公开工具链下载、校验和安全提取测试。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile

import pytest

from scripts import prepare_toolchain


def test_verify_file_rejects_wrong_size_and_hash(tmp_path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"locked")

    with pytest.raises(RuntimeError, match="大小校验失败"):
        prepare_toolchain._verify_file(
            artifact,
            {"size": 5},
            label="test",
        )

    with pytest.raises(RuntimeError, match="SHA-256 校验失败"):
        prepare_toolchain._verify_file(
            artifact,
            {"size": 6, "sha256": "0" * 64},
            label="test",
        )


def test_extract_nested_toolchains_selects_exact_basename(tmp_path) -> None:
    sdk = tmp_path / "sdk.tar.gz"
    expected = "toolchains-windows-x64-test.zip"
    payload = b"toolchains"
    with tarfile.open(sdk, mode="w:gz") as archive:
        ignored = tarfile.TarInfo("sdk/linux/toolchains-linux.zip")
        ignored.size = 5
        archive.addfile(ignored, io.BytesIO(b"linux"))
        selected = tarfile.TarInfo(f"sdk/windows/{expected}")
        selected.size = len(payload)
        archive.addfile(selected, io.BytesIO(payload))

    output = tmp_path / expected
    prepare_toolchain._extract_nested_toolchains(
        sdk,
        expected_name=expected,
        destination=output,
    )

    assert output.read_bytes() == payload


def test_extract_openharmony_files_checks_locked_hash(tmp_path) -> None:
    archive_path = tmp_path / "toolchains.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("sdk/toolchains/hdc.exe", b"hdc")
        archive.writestr("sdk/toolchains/lib/hap-sign-tool.jar", b"signer")

    output = tmp_path / "prepared"
    prepare_toolchain._extract_openharmony_files(
        archive_path,
        output,
        {
            "bin/hdc.exe": {
                "archive_suffix": "hdc.exe",
                "sha256": hashlib.sha256(b"hdc").hexdigest(),
            },
            "lib/hap-sign-tool.jar": {
                "archive_suffix": "lib/hap-sign-tool.jar",
                "sha256": hashlib.sha256(b"signer").hexdigest(),
            },
        },
    )

    assert (output / "bin" / "hdc.exe").read_bytes() == b"hdc"
    assert (output / "lib" / "hap-sign-tool.jar").read_bytes() == b"signer"


def test_safe_extract_zip_rejects_parent_traversal(tmp_path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, mode="w") as archive:
        archive.writestr("../escaped.txt", "unsafe")

    with pytest.raises(RuntimeError, match="越界路径"):
        prepare_toolchain._safe_extract_zip(archive_path, tmp_path / "target")

    assert not (tmp_path / "escaped.txt").exists()


def test_extract_jdk_tar_preserves_executable(tmp_path) -> None:
    archive_path = tmp_path / "jdk.tar.gz"
    payload = b"jlink"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        member = tarfile.TarInfo("jdk-21/bin/jlink")
        member.mode = 0o755
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    output = tmp_path / "jdk"
    prepare_toolchain._extract_jdk_archive(archive_path, output)

    jlink = output / "jdk-21" / "bin" / "jlink"
    assert jlink.read_bytes() == payload
    if os.name != "nt":
        assert jlink.stat().st_mode & stat.S_IXUSR


def test_safe_extract_tar_supports_early_python_311(
    tmp_path,
    monkeypatch,
) -> None:
    archive_path = tmp_path / "jdk.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        member = tarfile.TarInfo("jdk-21/release")
        member.size = 6
        archive.addfile(member, io.BytesIO(b"locked"))
    monkeypatch.delattr(prepare_toolchain.tarfile, "data_filter", raising=False)

    output = tmp_path / "output"
    prepare_toolchain._safe_extract_tar(archive_path, output)

    assert (output / "jdk-21" / "release").read_bytes() == b"locked"


def test_safe_extract_tar_rejects_parent_traversal(tmp_path) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        member = tarfile.TarInfo("../escaped.txt")
        member.size = 6
        archive.addfile(member, io.BytesIO(b"unsafe"))

    with pytest.raises(RuntimeError, match="越界路径"):
        prepare_toolchain._safe_extract_tar(archive_path, tmp_path / "target")

    assert not (tmp_path / "escaped.txt").exists()


def test_safe_extract_tar_rejects_special_files(tmp_path) -> None:
    archive_path = tmp_path / "unsafe-special.tar.gz"
    with tarfile.open(archive_path, mode="w:gz") as archive:
        member = tarfile.TarInfo("jdk-21/pipe")
        member.type = tarfile.FIFOTYPE
        archive.addfile(member)

    with pytest.raises(RuntimeError, match="特殊文件"):
        prepare_toolchain._safe_extract_tar(archive_path, tmp_path / "target")


def test_lock_includes_verified_linux_x64_toolchain() -> None:
    lock = json.loads(prepare_toolchain.LOCK_PATH.read_text(encoding="utf-8"))

    linux = lock["platforms"]["linux"]
    assert linux["architecture"] == "x64"
    assert linux["openharmony"]["toolchains_member"].startswith("toolchains-linux-x64-")
    assert len(linux["openharmony"]["toolchains_sha256"]) == 64
    assert linux["openharmony"]["files"]["bin/hdc"]["executable"] is True
    assert linux["java"]["archive"]["url"].endswith(".tar.gz")
    assert len(linux["java"]["archive"]["sha256"]) == 64


def test_prepare_rejects_host_architecture_mismatch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(prepare_toolchain, "_platform_tag", lambda: "linux")
    monkeypatch.setattr(prepare_toolchain, "_architecture_tag", lambda: "arm64")

    with pytest.raises(RuntimeError, match="架构不匹配"):
        prepare_toolchain.prepare(
            target_platform="linux",
            cache_dir=tmp_path / "cache",
            output=tmp_path / "output",
            sdk_archive_override=None,
            jdk_archive_override=None,
            force=False,
        )
