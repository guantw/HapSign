"""外部进程窗口策略与进程树终止测试。"""

import os
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest

from hapsign import subprocess_utils
from hapsign.cancellation import OperationCancelled


def test_windows_commands_do_not_create_console_window(monkeypatch) -> None:
    monkeypatch.setattr(subprocess_utils.platform, "system", lambda: "Windows")

    assert subprocess_utils.no_window_kwargs() == {
        "creationflags": subprocess_utils._CREATE_NO_WINDOW
    }


def test_other_platforms_do_not_receive_windows_flags(monkeypatch) -> None:
    monkeypatch.setattr(subprocess_utils.platform, "system", lambda: "Linux")

    assert subprocess_utils.no_window_kwargs() == {}


def test_cancelled_command_does_not_start_process(monkeypatch) -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    popen = Mock()
    monkeypatch.setattr(subprocess_utils, "no_window_kwargs", lambda: {})
    monkeypatch.setattr(subprocess_utils.subprocess, "Popen", popen)

    with pytest.raises(OperationCancelled):
        subprocess_utils.run_process(["tool"], cancel_event=cancel_event)

    popen.assert_not_called()


def _spawning_child_code(out_file: str) -> str:
    """生成子进程代码：拉起一个挂起 300s 的孙进程，并把其 PID 写入文件。

    孙进程 stdout/stderr 重定向到 DEVNULL，避免持有子进程的管道句柄导致
    取消后 communicate 无法读到 EOF（若进程树终止失效，测试会因此卡住而非误报）。
    """
    grandchild = "import time; time.sleep(300)"
    return (
        "import pathlib, subprocess, sys, time;"
        f"g = subprocess.Popen([sys.executable, '-c', {grandchild!r}],"
        " stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL);"
        f"pathlib.Path({out_file!r}).write_text(str(g.pid));"
        "time.sleep(300)"
    )


def _process_exists(pid: int) -> bool:
    """判断进程是否存活。Windows 上用 OpenProcess（os.kill(pid,0) 在进程不存在
    时会抛 SystemError），POSIX 上用 kill(pid, 0)。"""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _wait_pid_exit(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.05)
    return False


def test_cancel_terminates_whole_process_tree(tmp_path) -> None:
    out = tmp_path / "grandchild.pid"
    cancel_event = threading.Event()

    def cancel_after_grandchild_spawns() -> None:
        deadline = time.monotonic() + 10
        while not out.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        cancel_event.set()

    threading.Thread(target=cancel_after_grandchild_spawns, daemon=True).start()
    with pytest.raises(OperationCancelled):
        subprocess_utils.run_process(
            [sys.executable, "-c", _spawning_child_code(str(out))],
            capture_output=True,
            text=True,
            cancel_event=cancel_event,
        )

    assert out.exists(), "子进程未启动孙进程"
    grandchild_pid = int(out.read_text())
    assert _wait_pid_exit(grandchild_pid), "取消后孙进程仍存活"

    # 取消后下一次命令仍可正常运行
    result = subprocess_utils.run_process(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_timeout_terminates_whole_process_tree(tmp_path) -> None:
    out = tmp_path / "grandchild.pid"

    with pytest.raises(subprocess.TimeoutExpired):
        subprocess_utils.run_process(
            [sys.executable, "-c", _spawning_child_code(str(out))],
            capture_output=True,
            text=True,
            timeout=3.0,
        )

    assert out.exists(), "子进程未在超时前启动孙进程"
    grandchild_pid = int(out.read_text())
    assert _wait_pid_exit(grandchild_pid), "超时后孙进程仍存活"


def test_stop_process_terminates_tree_via_job(monkeypatch) -> None:
    process = Mock()
    process.poll.return_value = None
    terminated = []
    tree_killed = []
    # 该用例覆盖 Windows 的 Job Object + taskkill 路径，不依赖 CI 主机系统。
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(subprocess_utils, "_terminate_job", terminated.append)
    monkeypatch.setattr(subprocess_utils, "_terminate_windows_tree", tree_killed.append)

    subprocess_utils._stop_process(process, job="job")

    assert terminated == ["job"]
    # 无论 job 是否命中，Windows 都会补一发 taskkill /T 兜底
    assert tree_killed == [process.pid]
    process.wait.assert_called_once_with(timeout=2)


def test_stop_process_falls_back_to_process_group(monkeypatch) -> None:
    process = Mock()
    process.poll.return_value = None
    killed = []
    monkeypatch.setattr(subprocess_utils, "_terminate_process_group", killed.append)
    monkeypatch.setattr(subprocess_utils.os, "name", "posix")

    subprocess_utils._stop_process(process)

    assert killed == [process]
    process.wait.assert_called_once_with(timeout=2)


def test_stop_process_falls_back_to_windows_tree_kill(monkeypatch) -> None:
    process = Mock()
    process.poll.return_value = None
    tree_killed = []
    monkeypatch.setattr(subprocess_utils, "_terminate_windows_tree", tree_killed.append)
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")

    subprocess_utils._stop_process(process)

    assert tree_killed == [process.pid]
    process.wait.assert_called_once_with(timeout=2)
