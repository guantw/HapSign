"""检测 HAP 是否已包含签名块。

对齐 OpenHarmony ``developtools_hapsigner``：
- Java ``ZipUtils.findEocdInHap`` / ``HapUtils.findHapSigningBlock``
- C++ ``HapSigningBlockUtils::FindHapSigningBlock`` / ``CheckSignBlockHead``

仅做 presence 检测（结构上是否存在 Signing Block），不做 CMS / 证书链验签。
完整验签请使用 ``hap-sign-tool verify-app``，不适合 pipeline 门禁。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

# HapUtils / HapSignerBlockUtils 常量
HAP_SIGN_SCHEME_V3_BLOCK_VERSION = 3
HAP_SIG_BLOCK_MAGIC_LO_V2 = 0x2067695320504148
HAP_SIG_BLOCK_MAGIC_HI_V2 = 0x3234206B636F6C42
HAP_SIG_BLOCK_MAGIC_LO_V3 = 0x676973207061683C
HAP_SIG_BLOCK_MAGIC_HI_V3 = 0x3E6B636F6C62206E
HAP_SIG_BLOCK_HEADER_SIZE = 32
OPTIONAL_SUB_BLOCK_HEADER_SIZE = 12
# C++ HapSigningBlockUtils
MAX_BLOCK_COUNT = 10
MAX_HAP_SIGN_BLOCK_SIZE = 1024 * 1024 * 1024
# Java HapUtils.verifySignBlock: size <= Integer.MAX_VALUE - 8
_JAVA_MAX_SIG_BLOCK_SIZE = 0x7FFFFFFF - 8

# ZipUtils
ZIP_EOCD_SEGMENT_MIN_SIZE = 22
ZIP_EOCD_COMMENT_LENGTH_OFFSET = 20
ZIP_CD_SIZE_OFFSET_IN_EOCD = 12
ZIP_CD_OFFSET_IN_EOCD = 16
ZIP_EOCD_MAX_COMMENT_SIZE = 0xFFFF
ZIP64_EOCD_LOCATOR_SIZE = 20
ZIP64_EOCD_LOCATOR_SIG = 0x07064B50
ZIP64_UINT32_SENTINEL = 0xFFFFFFFF
_EOCD_SIGNATURE = b"PK\x05\x06"


def _find_eocd_in_window(window: bytes) -> int | None:
    """在搜索窗口内定位 EOCD，用 comment length 自洽性排除伪签名。"""
    search_size = len(window)
    if search_size < ZIP_EOCD_SEGMENT_MIN_SIZE:
        return None
    relative = window.rfind(_EOCD_SIGNATURE)
    while relative >= 0:
        if relative + ZIP_EOCD_SEGMENT_MIN_SIZE <= search_size:
            comment_size = struct.unpack_from(
                "<H", window, relative + ZIP_EOCD_COMMENT_LENGTH_OFFSET
            )[0]
            if relative + ZIP_EOCD_SEGMENT_MIN_SIZE + comment_size == search_size:
                return relative
        relative = window.rfind(_EOCD_SIGNATURE, 0, relative)
    return None


def _find_eocd_offset(
    hap_file: BinaryIO, file_size: int, max_comment_size: int
) -> int | None:
    """按给定最大 comment 长度从文件末尾搜索 EOCD。"""
    if file_size < ZIP_EOCD_SEGMENT_MIN_SIZE:
        return None
    if max_comment_size < 0 or max_comment_size > ZIP_EOCD_MAX_COMMENT_SIZE:
        raise ValueError(f"max_comment_size out of range: {max_comment_size}")

    final_max_comment = min(max_comment_size, file_size - ZIP_EOCD_SEGMENT_MIN_SIZE)
    search_size = final_max_comment + ZIP_EOCD_SEGMENT_MIN_SIZE
    search_start = file_size - search_size
    hap_file.seek(search_start)
    window = hap_file.read(search_size)
    if len(window) != search_size:
        return None
    relative = _find_eocd_in_window(window)
    if relative is None:
        return None
    return search_start + relative


def _find_eocd_offset_two_phase(hap_file: BinaryIO, file_size: int) -> int | None:
    """对齐 ZipUtils.findEocdInHap：先假设无 comment，再扩大到 65535。"""
    eocd = _find_eocd_offset(hap_file, file_size, 0)
    if eocd is not None:
        return eocd
    return _find_eocd_offset(hap_file, file_size, ZIP_EOCD_MAX_COMMENT_SIZE)


def _has_zip64_eocd_locator(hap_file: BinaryIO, eocd_offset: int) -> bool:
    """对齐 ZipUtils.checkZip64EoCDLocatorIsPresent。"""
    locator_pos = eocd_offset - ZIP64_EOCD_LOCATOR_SIZE
    if locator_pos < 0:
        return False
    hap_file.seek(locator_pos)
    sig = hap_file.read(4)
    if len(sig) != 4:
        return False
    return struct.unpack("<I", sig)[0] == ZIP64_EOCD_LOCATOR_SIG


def _is_valid_signing_header(header: bytes, central_directory_offset: int) -> bool:
    """校验 32 字节签名块 footer：magic / version / size / blockCount。"""
    if len(header) != HAP_SIG_BLOCK_HEADER_SIZE:
        return False
    block_count, block_size, magic_lo, magic_hi, version = struct.unpack(
        "<iqqqi", header
    )
    if version >= HAP_SIGN_SCHEME_V3_BLOCK_VERSION:
        magic_ok = (
            magic_lo == HAP_SIG_BLOCK_MAGIC_LO_V3
            and magic_hi == HAP_SIG_BLOCK_MAGIC_HI_V3
        )
    else:
        magic_ok = (
            magic_lo == HAP_SIG_BLOCK_MAGIC_LO_V2
            and magic_hi == HAP_SIG_BLOCK_MAGIC_HI_V2
        )
    if not magic_ok:
        return False

    # 真实签名包至少有一个 sub-block；0/负数视为非法
    if block_count <= 0 or block_count > MAX_BLOCK_COUNT:
        return False

    max_size = min(
        central_directory_offset,
        MAX_HAP_SIGN_BLOCK_SIZE,
        _JAVA_MAX_SIG_BLOCK_SIZE,
    )
    if not HAP_SIG_BLOCK_HEADER_SIZE <= block_size <= max_size:
        return False

    # sub-block header 数组必须能放进签名块主体（footer 之外）
    block_array_size = block_size - HAP_SIG_BLOCK_HEADER_SIZE
    if block_count * OPTIONAL_SUB_BLOCK_HEADER_SIZE > block_array_size:
        return False
    return True


def is_hap_signed(hap_path: str | Path) -> bool:
    """判断 HAP 是否已包含 Hap Signing Block。

    仅检测签名块是否存在，不做证书链或 Profile 校验。
    文件不存在、ZIP64、非 ZIP、或结构非法时返回 False（宁可漏判也不误判）。
    """
    path = Path(hap_path)
    try:
        with path.open("rb") as hap_file:
            hap_file.seek(0, 2)
            file_size = hap_file.tell()
            eocd_offset = _find_eocd_offset_two_phase(hap_file, file_size)
            if eocd_offset is None:
                return False

            # ZIP64：上游 verify 路径直接拒绝；我们同样不做深入解析
            if _has_zip64_eocd_locator(hap_file, eocd_offset):
                return False

            hap_file.seek(eocd_offset)
            eocd = hap_file.read(ZIP_EOCD_SEGMENT_MIN_SIZE)
            if len(eocd) != ZIP_EOCD_SEGMENT_MIN_SIZE:
                return False

            central_directory_size = struct.unpack_from(
                "<I", eocd, ZIP_CD_SIZE_OFFSET_IN_EOCD
            )[0]
            central_directory_offset = struct.unpack_from(
                "<I", eocd, ZIP_CD_OFFSET_IN_EOCD
            )[0]
            if (
                central_directory_size == ZIP64_UINT32_SENTINEL
                or central_directory_offset == ZIP64_UINT32_SENTINEL
            ):
                return False
            # CD 必须紧贴 EOCD（与 ZipUtils.findZipInfo / HapUtils 一致）
            if (
                central_directory_offset < HAP_SIG_BLOCK_HEADER_SIZE
                or central_directory_offset + central_directory_size != eocd_offset
            ):
                return False

            header_offset = central_directory_offset - HAP_SIG_BLOCK_HEADER_SIZE
            hap_file.seek(header_offset)
            header = hap_file.read(HAP_SIG_BLOCK_HEADER_SIZE)
            return _is_valid_signing_header(header, central_directory_offset)
    except OSError:
        return False
