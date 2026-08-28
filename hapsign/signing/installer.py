"""HDC 工具封装 —— 获取设备 UDID 和安装 hap。"""

from __future__ import annotations

import datetime
import logging
import os
import re
import subprocess
import threading
import time

from hapsign import config
from hapsign.subprocess_utils import no_window_kwargs, run_process

logger = logging.getLogger(__name__)

_HDC_SERVER_HOST = "127.0.0.1"
_HDC_SERVER_PORT = 8710

# hdc start 调用时刻与监听进程创建时刻比较时允许的时钟偏差（秒）
_CLOCK_SKEW_SECONDS = 2.0

# hdc start 返回后确认监听进程出现的轮询参数（HDC 启动可能略慢）
_LISTENER_POLL_ATTEMPTS = 8
_LISTENER_POLL_INTERVAL = 0.25

# hdc install 的失败标记：非零退出码之外，仅当输出含这些真实失败指示才判定失败。
# - [Fail] 状态标记、INSTALL_FAILED_ 前缀可在任意位置出现（HDC 的错误码前缀）；
# - error: 只匹配状态行行首，避免普通输出里的类似字符串误判。
_FATAL_INSTALL_MARKERS = re.compile(
    r"(?:\[fail\]|install_failed_|^error:)", re.IGNORECASE | re.MULTILINE
)


def _listener_pid() -> int | None:
    """返回监听 ``127.0.0.1:8710`` 的进程 PID；无监听或无法识别时返回 None。"""
    if os.name == "nt":
        return _listener_pid_windows()
    return _listener_pid_posix()


def _listener_pid_windows() -> int | None:
    """解析 netstat 输出，取本地监听 8710 的 PID。"""
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=10,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # 监听套接字的远端地址恒为 0.0.0.0:0，状态列文本在不同语言环境可能不同
    pattern = re.compile(r"TCP\s+127\.0\.0\.1:8710\s+0\.0\.0\.0:0\s+\S+\s+(\d+)\s*$")
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            return int(match.group(1))
    return None


def _listener_pid_posix() -> int | None:
    """POSIX 平台回退使用 lsof 定位监听 8710 的 PID。"""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{_HDC_SERVER_PORT}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines()[1:]:  # 跳过表头
        parts = line.split()
        if len(parts) >= 2:
            try:
                return int(parts[1])
            except ValueError:
                continue
    return None


def _process_start_time(pid: int) -> float | None:
    """返回进程启动时刻的 epoch 秒；无法读取时返回 None。"""
    if os.name == "nt":
        return _process_start_time_windows(pid)
    return _process_start_time_posix(pid)


def _process_start_time_windows(pid: int) -> float | None:
    """用 GetProcessTimes 读取进程创建时间（epoch 秒）。"""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    process = kernel32.OpenProcess(
        0x1000,
        False,
        pid,  # PROCESS_QUERY_LIMITED_INFORMATION
    )
    if not process:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            process,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        raw = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        # FILETIME 从 1601-01-01 UTC 起以 100ns 计，换算为 Unix epoch 秒
        return (raw - 116444736000000000) / 10_000_000.0
    finally:
        kernel32.CloseHandle(process)


def _process_start_time_posix(pid: int) -> float | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout.strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.strptime(text, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return time.mktime(parsed.timetuple())


class Installer:
    """使用 hdc 获取设备 UDID 并安装 hap 包。"""

    def __init__(self, cancel_event: threading.Event | None = None) -> None:
        self._hdc = config.HDC_PATH
        self.cancel_event = cancel_event
        # 本任务确认创建、close 时应清理的 HDC server 监听 PID；None 表示不归属
        self._owned_server_pid: int | None = None
        self._server_checked = False
        self._closed = False

    def __enter__(self) -> Installer:
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    def _ensure_server(self) -> None:
        """确保 HDC server 可用，并确认本任务是否创建了它。仅首次使用时执行。

        - 已存在监听进程（既有 HDC 或非 HDC 占用）：不启动、不接管；端口被非
          HDC 占用的情况由后续 hdc 命令失败自然暴露。
        - 无监听进程：显式 ``hdc start``；随后短轮询等待监听进程出现（HDC 启动
          可能略慢），仅当出现的监听进程“创建时刻不早于本次调用”才确认归属并
          记录 PID。
        - 启动失败或归属无法确认：记录警告，``close`` 不做清理。
        """
        if self._server_checked:
            return
        self._server_checked = True

        pre = _listener_pid()
        if pre is not None:
            logger.info("检测到既有 HDC server (PID %s)，本次任务不接管", pre)
            return

        started_at = time.time()
        try:
            result = subprocess.run(
                [self._hdc, "start"],
                capture_output=True,
                text=True,
                timeout=10,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("启动 HDC 后台服务失败: %s", exc)
            return
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            logger.warning(
                "启动 HDC 后台服务失败 (code=%s): %s",
                result.returncode,
                output,
            )
            return

        post = None
        for attempt in range(_LISTENER_POLL_ATTEMPTS):
            post = _listener_pid()
            if post is not None:
                break
            if attempt + 1 < _LISTENER_POLL_ATTEMPTS:
                time.sleep(_LISTENER_POLL_INTERVAL)
        if post is None:
            logger.warning("hdc start 未发现监听进程，归属不明，close 时不清理")
            return
        created = _process_start_time(post)
        if created is None or created < started_at - _CLOCK_SKEW_SECONDS:
            # 监听进程早于本次 hdc start 创建 → 外部 HDC 抢先启动，不接管
            logger.warning(
                "监听进程 PID %s 非本次启动（可能外部抢先），不接管清理", post
            )
            return
        self._owned_server_pid = post
        logger.info("本任务已启动 HDC 后台服务 (PID %s)", post)

    def close(self) -> None:
        """仅关闭本实例确认启动的 HDC server。

        先复核监听 PID 未易主，再调用全局 ``hdc kill`` 优雅关闭（官方命令，
        由守护进程自身结束其进程树）；归属不明或已易主时不清理。
        """
        if self._closed:
            return
        self._closed = True
        if self._owned_server_pid is None:
            return

        current = _listener_pid()
        if current != self._owned_server_pid:
            if current is None:
                logger.info("HDC server 已不在监听，跳过清理")
            else:
                logger.warning(
                    "HDC server 监听 PID 已从 %s 变为 %s，视为外部接管，不执行清理",
                    self._owned_server_pid,
                    current,
                )
            return

        try:
            result = subprocess.run(
                [self._hdc, "kill"],
                capture_output=True,
                text=True,
                timeout=5,
                **no_window_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.warning("关闭 HDC 后台服务失败: %s", exc)
            return
        if result.returncode == 0:
            logger.info(
                "已关闭本次启动的 HDC 后台服务 (PID %s)", self._owned_server_pid
            )
        else:
            output = (result.stderr or result.stdout or "").strip()
            logger.warning(
                "关闭 HDC 后台服务失败 (code=%s): %s",
                result.returncode,
                output,
            )

    def get_udid(self) -> str:
        """获取已连接设备的 UDID。

        使用 ``hdc shell bm get -u`` 命令获取设备标识。
        输出形如::

            udid of current device is :
            5BC00B489B1B0A5A6687A1DD55918BC18FC75568AAEE07DF969B196F80DEBF46

        从输出中提取 64 位十六进制 UDID。

        Returns:
            UDID 字符串（64 位十六进制）。

        Raises:
            RuntimeError: 没有可用设备或无法获取 UDID 时抛出。
        """
        self._ensure_server()
        commands = [
            [self._hdc, "shell", "bm", "get", "-u"],
            [self._hdc, "shell", "param", "get", "const.product.udid"],
        ]
        for cmd in commands:
            try:
                result = run_process(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    cancel_event=self.cancel_event,
                )
            except subprocess.TimeoutExpired:
                continue
            if result.returncode == 0:
                # 从输出中提取 64 位十六进制 UDID
                match = re.search(r"\b([0-9A-Fa-f]{64})\b", result.stdout)
                if match:
                    return match.group(1)
        raise RuntimeError(
            "未检测到可用设备：请确认设备已连接、已授权 USB 调试，且当前只连接一台设备"
        )

    def install(self, hap_path: str) -> bool:
        """使用 hdc install 安装 hap 包到已连接设备。

        Args:
            hap_path: 本地 hap 文件路径。

        Returns:
            成功返回 True。

        Raises:
            RuntimeError: hdc install 执行失败时抛出。
        """
        self._ensure_server()
        cmd = [self._hdc, "install", hap_path]
        result = run_process(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cancel_event=self.cancel_event,
        )
        output = (result.stdout or "") + (result.stderr or "")
        # hdc install 即使失败也可能返回 0，仅按真实失败标记识别：
        # 非零退出码、[Fail] 状态行、INSTALL_FAILED_ 前缀或明确的 error: 行。
        failed = result.returncode != 0 or bool(_FATAL_INSTALL_MARKERS.search(output))
        if failed:
            raise RuntimeError(
                f"hdc install 失败 (code={result.returncode}): {output.strip()}"
            )
        return True
