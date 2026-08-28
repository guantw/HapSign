"""Token 缓存静态加密。

Windows 使用当前用户作用域的 DPAPI（CryptProtectData / CryptUnprotectData），
无第三方依赖；其他平台退化为受限权限（0o600）的明文 JSON，并在日志与文档中
明确告警。缓存文件单行存储：

- 加密格式：``hapsign-token-v1:<base64(DATA_BLOB)>``
- 旧版明文 JSON 首次读取时自动安全迁移为加密格式。

任何路径都不得把加密前的 token、密钥或完整缓存内容写入日志。
"""

from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

_HEADER = "hapsign-token-v1:"
_HEADER_BYTES = _HEADER.encode("ascii")

# CRYPTPROTECT_UI_FORBIDDEN：禁止弹出任何 UI，失败即抛错。
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class DecryptError(Exception):
    """缓存无法解密（密钥不可用、内容损坏或非加密格式）。"""


def is_encrypted(data: bytes) -> bool:
    """判断缓存字节是否为加密格式。"""
    return data.startswith(_HEADER_BYTES)


def protect(payload: bytes) -> bytes:
    """加密缓存载荷，返回可直接写入磁盘的字节。

    非 Windows 平台无 DPAPI，按约定退化为原样明文（由调用方限制文件权限），
    并记录明确告警。Windows 上 CryptProtectData 失败时抛出 OSError，由调用方
    决定降级策略（当前策略：放弃保存并告警，绝不把 token 明文落盘）。
    """
    if os.name != "nt":
        logger.warning(
            "当前平台没有 DPAPI，token 缓存将以受限权限的明文存储；"
            "请勿在共享电脑或云同步目录中使用本工具"
        )
        return payload
    return _HEADER_BYTES + base64.b64encode(_dpapi_protect(payload))


def decrypt(data: bytes) -> bytes:
    """解密缓存字节，返回原始 JSON 载荷。

    Raises:
        DecryptError: 非加密格式、非 Windows 平台、base64 无效或 DPAPI 解密失败。
    """
    if not is_encrypted(data):
        raise DecryptError("缓存不是加密格式")
    if os.name != "nt":
        # 加密缓存由 Windows DPAPI 生成，跨平台无法解密。显式报错而不是
        # 去调用仅 Windows 可用的 crypt32，避免 AttributeError 泄漏。
        raise DecryptError(
            "缓存文件是 Windows DPAPI 加密格式，当前平台无法解密；"
            "请删除缓存文件后重新登录"
        )
    try:
        blob = base64.b64decode(data[len(_HEADER_BYTES) :])
    except ValueError as exc:
        raise DecryptError("缓存头有效但 base64 编码无效") from exc
    try:
        return _dpapi_unprotect(blob)
    except OSError as exc:
        raise DecryptError(f"DPAPI 解密失败: {exc}") from exc


def _dpapi_protect(data: bytes) -> bytes:
    """调用 CryptProtectData（当前用户作用域）加密字节串。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HANDLE]

    buffer = ctypes.create_string_buffer(data)
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    blob_out = DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise OSError(f"CryptProtectData 失败: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    """调用 CryptUnprotectData（当前用户作用域）解密字节串。"""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [wintypes.HANDLE]

    buffer = ctypes.create_string_buffer(data)
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(blob_out),
    ):
        raise OSError(f"CryptUnprotectData 失败: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
