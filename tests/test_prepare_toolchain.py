"""公开工具链下载、校验和安全提取测试。"""

from __future__ import annotations

import hashlib
import io
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
