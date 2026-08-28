"""HAP 签名块检测测试。"""

import struct
import zipfile
from pathlib import Path

from hapsign.signing.hap_inspect import (
    HAP_SIG_BLOCK_HEADER_SIZE,
    HAP_SIG_BLOCK_MAGIC_HI_V2,
    HAP_SIG_BLOCK_MAGIC_HI_V3,
    HAP_SIG_BLOCK_MAGIC_LO_V2,
    HAP_SIG_BLOCK_MAGIC_LO_V3,
    HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
    MAX_BLOCK_COUNT,
    ZIP64_EOCD_LOCATOR_SIG,
    ZIP64_EOCD_LOCATOR_SIZE,
    is_hap_signed,
)


def _write_unsigned_hap(path: Path, *, comment: bytes = b"") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "module.json",
            '{"app":{"bundleName":"com.example.app"}}',
        )
        archive.comment = comment


def _find_eocd_offset(data: bytes) -> int:
    """定位测试包的真实 EOCD，忽略 comment 内的伪签名。"""
    relative = data.rfind(b"PK\x05\x06")
    while relative >= 0:
        if relative + 22 <= len(data):
            comment_size = struct.unpack_from("<H", data, relative + 20)[0]
            if relative + 22 + comment_size == len(data):
                return relative
        relative = data.rfind(b"PK\x05\x06", 0, relative)
    raise AssertionError("EOCD not found")


def _inject_signing_block(
    path: Path,
    *,
    magic_lo: int,
    magic_hi: int,
    version: int,
    block_size: int | None = None,
    block_count: int = 1,
) -> None:
    """在 ZIP Central Directory 前插入伪造的 HAP Signing Block header。"""
    data = bytearray(path.read_bytes())
    eocd_offset = _find_eocd_offset(data)
    cd_offset = struct.unpack_from("<I", data, eocd_offset + 16)[0]
    size = block_size if block_size is not None else HAP_SIG_BLOCK_HEADER_SIZE
    if size < HAP_SIG_BLOCK_HEADER_SIZE:
        raise ValueError("block_size too small")
    # 与 OpenHarmony HapUtils 一致：int + long + long + long + int
    header = struct.pack(
        "<iqqqi",
        block_count,
        size,  # hapSigBlockSize
        magic_lo,
        magic_hi,
        version,
    )
    assert len(header) == HAP_SIG_BLOCK_HEADER_SIZE
    block = bytearray(size)
    block[-HAP_SIG_BLOCK_HEADER_SIZE:] = header
    data[cd_offset:cd_offset] = block
    new_cd_offset = cd_offset + size
    new_eocd_offset = eocd_offset + size
    struct.pack_into("<I", data, new_eocd_offset + 16, new_cd_offset)
    path.write_bytes(data)


def test_unsigned_hap_is_not_signed(tmp_path: Path) -> None:
    hap = tmp_path / "app.hap"
    _write_unsigned_hap(hap)

    assert is_hap_signed(str(hap)) is False


def test_v3_signing_block_is_detected(tmp_path: Path) -> None:
    hap = tmp_path / "app.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
    )

    assert is_hap_signed(str(hap)) is True


def test_v2_signing_block_is_detected(tmp_path: Path) -> None:
    hap = tmp_path / "app.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V2,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V2,
        version=2,
        block_size=48,
    )

    assert is_hap_signed(str(hap)) is True


def test_invalid_magic_is_not_signed(tmp_path: Path) -> None:
    hap = tmp_path / "app.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=0,
        magic_hi=0,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
    )

    assert is_hap_signed(str(hap)) is False


def test_eocd_signature_inside_comment_is_ignored(tmp_path: Path) -> None:
    hap = tmp_path / "comment.hap"
    _write_unsigned_hap(hap, comment=b"contains-PK\x05\x06-marker")
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
    )

    assert is_hap_signed(hap) is True


def test_invalid_central_directory_layout_is_not_signed(tmp_path: Path) -> None:
    hap = tmp_path / "invalid-directory.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
    )
    data = bytearray(hap.read_bytes())
    eocd_offset = _find_eocd_offset(data)
    struct.pack_into("<I", data, eocd_offset + 12, 0)
    hap.write_bytes(data)

    assert is_hap_signed(hap) is False


def test_zero_block_count_is_not_signed(tmp_path: Path) -> None:
    hap = tmp_path / "zero-blocks.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
        block_count=0,
    )

    assert is_hap_signed(hap) is False


def test_block_count_must_fit_inside_signing_block(tmp_path: Path) -> None:
    hap = tmp_path / "too-many-blocks.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
        block_count=3,
    )

    assert is_hap_signed(hap) is False


def test_block_count_above_max_is_not_signed(tmp_path: Path) -> None:
    hap = tmp_path / "max-blocks.hap"
    _write_unsigned_hap(hap)
    block_count = MAX_BLOCK_COUNT + 1
    block_size = HAP_SIG_BLOCK_HEADER_SIZE + block_count * 12 + 64
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=block_size,
        block_count=block_count,
    )

    assert is_hap_signed(hap) is False


def test_zip64_locator_bytes_before_eocd_are_rejected(tmp_path: Path) -> None:
    """对齐 VerifyHap：EOCD 前 20 字节若为 ZIP64 Locator 则不支持。"""
    hap = tmp_path / "zip64-locator.hap"
    _write_unsigned_hap(hap)
    _inject_signing_block(
        hap,
        magic_lo=HAP_SIG_BLOCK_MAGIC_LO_V3,
        magic_hi=HAP_SIG_BLOCK_MAGIC_HI_V3,
        version=HAP_SIGN_SCHEME_V3_BLOCK_VERSION,
        block_size=64,
    )
    data = bytearray(hap.read_bytes())
    eocd_offset = _find_eocd_offset(data)
    locator_pos = eocd_offset - ZIP64_EOCD_LOCATOR_SIZE
    assert locator_pos >= 0
    struct.pack_into("<I", data, locator_pos, ZIP64_EOCD_LOCATOR_SIG)
    hap.write_bytes(data)

    assert is_hap_signed(hap) is False


def test_missing_file_is_not_signed(tmp_path: Path) -> None:
    assert is_hap_signed(str(tmp_path / "missing.hap")) is False
