"""跨平台外部进程启动选项。"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import threading
import time
from typing import Any

from hapsign.cancellation import OperationCancelled, raise_if_cancelled

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class ProcessOutputError(RuntimeError):
    """外部进程的已捕获输出无法按调用方声明的格式提供。"""


def _output_encoding(text: bool, options: dict[str, Any]) -> str:
    encoding = options.get("encoding")
    if encoding is not None:
        return str(encoding)
    if text or options.get("errors") is not None:
        return "Python default"
    return "bytes"


def _process_output_error(
    command: list[str],
    text: bool,
    options: dict[str, Any],
    detail: str,
) -> ProcessOutputError:
    # 只包含可执行文件名，避免把密码等敏感命令参数带入错误信息。
    executable = command[0] if command else "<unknown>"
    encoding = _output_encoding(text, options)
    return ProcessOutputError(
        f"外部命令 {executable!r} 的输出不可用 (encoding={encoding}): {detail}"
    )


def _validate_captured_output(
    result: subprocess.CompletedProcess,
    command: list[str],
    *,
    capture_output: bool,
    text: bool,
    options: dict[str, Any],
) -> subprocess.CompletedProcess:
    """捕获开启时保证 stdout/stderr 存在，否则给出明确的边界错误。"""
    if capture_output and (result.stdout is None or result.stderr is None):
        raise _process_output_error(
            command,
            text,
            options,
            "capture_output=True 但 stdout/stderr 为 None",
        )
    return result


def no_window_kwargs() -> dict[str, int]:
    """Windows 下禁止控制台工具创建一闪而过的命令行窗口。"""
    if platform.system() == "Windows":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


# ── Windows Job Object（进程树终止） ─────────────────────────────────

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


def _create_job_object(process: subprocess.Popen) -> Any | None:
    """为 Windows 子进程创建带 KILL_ON_JOB_CLOSE 的 Job Object。

    创建或绑定失败（例如已处于禁止嵌套的 Job 中）时返回 None，调用方回退为
    只终止直接子进程。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IOCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            return None
        if not kernel32.AssignProcessToJobObject(job, process._handle):
            kernel32.CloseHandle(job)
            return None
        return job
    except (OSError, AttributeError):
        return None


def _terminate_job(job: Any) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject(job, 1)
    except OSError:
        pass


def _close_job(job: Any) -> None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(job)
    except OSError:
        pass


# ── 进程树终止 ─────────────────────────────────────────────────────


def _terminate_process_group(process: subprocess.Popen) -> None:
    """POSIX：向子进程所在进程组发送 SIGTERM（进程以新会话/组启动）。"""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _terminate_windows_tree(pid: int) -> None:
    """Windows 兜底：taskkill /T 终止整棵进程树。

    Job Object 只能覆盖绑定时刻之后创建的进程，对绑定前已存在的后代进程
    无能为力；taskkill /T 在终止时重新遍历进程树，可补齐这部分进程。
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
    except (OSError, ValueError):
        pass


def _stop_process(
    process: subprocess.Popen,
    job: Any | None = None,
) -> None:
    """终止子进程及其后代进程树。

    Windows 依次使用 Job Object 与 taskkill /T 兜底；POSIX 使用进程组；
    最后等待最多 2 秒，仍存活则强制 kill。
    """
    if process.poll() is not None:
        return
    if job is not None:
        _terminate_job(job)
    if os.name == "nt":
        _terminate_windows_tree(process.pid)
    elif job is None:
        _terminate_process_group(process)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _popen_process_tree_options() -> dict[str, Any]:
    """Popen 需要额外传入的进程树参数：POSIX 下独立进程组。"""
    if os.name == "nt":
        return {}
    return {"start_new_session": True}


def run_process(
    command: list[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    capture_output: bool = False,
    text: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """运行外部命令；有取消信号或超时要求时，终止子进程及整棵进程树。

    仅当既无取消信号也无超时时走 subprocess.run 快路径；否则使用 Popen +
    Job Object / 进程组，确保取消或超时能终止整棵进程树。调用方要求捕获输出时，
    stdout/stderr 必须保持稳定；解码失败或异常的 None 输出会转换为明确异常。
    """
    options = no_window_kwargs()
    options.update(kwargs)
    if cancel_event is None and timeout is None:
        try:
            result = subprocess.run(
                command,
                capture_output=capture_output,
                text=text,
                **options,
            )
        except UnicodeError as exc:
            raise _process_output_error(command, text, options, str(exc)) from exc
        return _validate_captured_output(
            result,
            command,
            capture_output=capture_output,
            text=text,
            options=options,
        )

    raise_if_cancelled(cancel_event)
    popen_options = dict(options)
    popen_options.update(_popen_process_tree_options())
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        **popen_options,
    )
    job = _create_job_object(process) if os.name == "nt" else None
    try:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _stop_process(process, job)
                process.communicate()
                raise OperationCancelled("操作已取消")
            wait_timeout = 0.1
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _stop_process(process, job)
                    stdout, stderr = process.communicate()
                    raise subprocess.TimeoutExpired(
                        command,
                        timeout,
                        output=stdout,
                        stderr=stderr,
                    )
                wait_timeout = min(wait_timeout, remaining)
            try:
                stdout, stderr = process.communicate(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                continue
            except UnicodeError as exc:
                _stop_process(process, job)
                raise _process_output_error(command, text, options, str(exc)) from exc
            return _validate_captured_output(
                subprocess.CompletedProcess(
                    command,
                    process.returncode,
                    stdout,
                    stderr,
                ),
                command,
                capture_output=capture_output,
                text=text,
                options=options,
            )
    finally:
        if job is not None:
            _close_job(job)
