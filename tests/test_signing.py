"""外部签名与安装命令的无设备测试。"""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hapsign.signing import hap_signer, installer, keytool_util


def test_hap_signer_builds_subprocess_command(monkeypatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(hap_signer, "run_process", run)

    result = hap_signer.HapSigner().sign_hap(
        "in.hap", "cert.cer", "profile.p7b", "key.p12", output_path="out.hap"
    )

    command = run.call_args.args[0]
    assert result is True
    assert command[command.index("-inFile") + 1] == "in.hap"
    assert command[command.index("-outFile") + 1] == "out.hap"
    assert (
        command[command.index("-keystorePwd") + 1]
        == hap_signer.config.KEYSTORE_PASSWORD
    )


def test_keytool_defaults_use_config_keystore_password(monkeypatch, tmp_path) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(keytool_util, "run_process", run)
    monkeypatch.setattr(
        keytool_util.KeytoolUtil,
        "_get_keytool_path",
        staticmethod(lambda: "keytool"),
    )
    keystore = tmp_path / "debug.p12"

    keytool_util.KeytoolUtil().generate_keypair(str(keystore))

    command = run.call_args.args[0]
    assert command[command.index("-storepass") + 1] == (
        keytool_util.config.KEYSTORE_PASSWORD
    )


def test_hap_signer_reports_tool_failure(monkeypatch) -> None:
    result = SimpleNamespace(returncode=2, stdout="", stderr="sign failed")
    monkeypatch.setattr(hap_signer, "run_process", Mock(return_value=result))

    with pytest.raises(RuntimeError, match="sign failed"):
        hap_signer.HapSigner().sign_hap("in.hap", "cert.cer", "profile.p7b", "key.p12")


def test_installer_extracts_udid(monkeypatch) -> None:
    udid = "A" * 64
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=udid, stderr=""))
    monkeypatch.setattr(installer, "run_process", run)
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    assert installer.Installer().get_udid() == udid


def test_installer_targets_explicit_serial(monkeypatch) -> None:
    udid = "A" * 64
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=udid, stderr=""))
    monkeypatch.setattr(installer, "run_process", run)
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    assert installer.Installer(serial="device-serial").get_udid() == udid
    assert run.call_args.args[0][:3] == [
        installer.config.HDC_PATH,
        "-t",
        "device-serial",
    ]


def test_installer_lists_agent_friendly_targets(monkeypatch) -> None:
    result = SimpleNamespace(
        returncode=0,
        stdout=(
            "127.0.0.1:5555\t\tTCP\tConnected\tlocalhost\n"
            "5XQ0225613000233\t\tUSB\tConnected\tlocalhost\n"
            "offline-device\t\tUSB\tOffline\tlocalhost\n"
        ),
        stderr="",
    )
    run = Mock(return_value=result)
    monkeypatch.setattr(installer, "run_process", run)
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    targets = installer.Installer().list_targets(connected_only=True)

    assert [target["serial"] for target in targets] == [
        "127.0.0.1:5555",
        "5XQ0225613000233",
    ]
    assert targets[0]["likely_emulator"] is True
    assert targets[0]["physical_candidate"] is False
    assert targets[1]["likely_emulator"] is False
    assert targets[1]["physical_candidate"] is True
    assert targets[1]["localhost"] is False
    assert run.call_args.args[0] == [
        installer.config.HDC_PATH,
        "list",
        "targets",
        "-v",
    ]


def test_installer_inspects_bundle_on_explicit_serial(monkeypatch) -> None:
    result = SimpleNamespace(
        returncode=0,
        stdout=(
            '{"bundleName":"com.example.app","appProvisionType":"debug",'
            '"versionName":"1.2.3"}'
        ),
        stderr="",
    )
    run = Mock(return_value=result)
    monkeypatch.setattr(installer, "run_process", run)
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    bundle = installer.Installer(serial="device-serial").inspect_bundle(
        "com.example.app"
    )

    assert bundle == {
        "bundle_name": "com.example.app",
        "provision_type": "debug",
        "version_name": "1.2.3",
    }
    assert run.call_args.args[0] == [
        installer.config.HDC_PATH,
        "-t",
        "device-serial",
        "shell",
        "bm",
        "dump",
        "-n",
        "com.example.app",
    ]


def test_installer_raises_on_fail_marker(monkeypatch) -> None:
    # rc=0 但输出含 [Fail] 状态行 → 判定失败
    result = SimpleNamespace(
        returncode=0, stdout="[Fail]install bundle failed", stderr=""
    )
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    with pytest.raises(RuntimeError, match="hdc install"):
        installer.Installer().install("app.hap")


def test_installer_raises_on_install_failed_prefix(monkeypatch) -> None:
    result = SimpleNamespace(
        returncode=0,
        stdout="INSTALL_FAILED_MSG_BUFFER_ERROR: install failed",
        stderr="",
    )
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    with pytest.raises(RuntimeError, match="hdc install"):
        installer.Installer().install("app.hap")


def test_installer_raises_on_error_line(monkeypatch) -> None:
    result = SimpleNamespace(returncode=0, stdout="Error: signature invalid", stderr="")
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    with pytest.raises(RuntimeError, match="hdc install"):
        installer.Installer().install("app.hap")


def test_installer_accepts_error_text_not_at_line_start(monkeypatch) -> None:
    # 普通输出里含 "error:" 但不在状态行行首 → 不得误判为失败
    result = SimpleNamespace(
        returncode=0,
        stdout="[Info]connect success: 127.0.0.1:8710 error: nothing to worry\n"
        "[Info]install bundle successfully",
        stderr="",
    )
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    assert installer.Installer().install("app.hap") is True


def test_installer_raises_on_nonzero_exit(monkeypatch) -> None:
    result = SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    with pytest.raises(RuntimeError, match="hdc install"):
        installer.Installer().install("app.hap")


def test_installer_requires_success_text(monkeypatch) -> None:
    result = SimpleNamespace(returncode=0, stdout="transfer finished", stderr="")
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    with pytest.raises(RuntimeError, match="hdc install"):
        installer.Installer().install("app.hap")


def test_installer_uses_serial_and_replace(monkeypatch) -> None:
    result = SimpleNamespace(
        returncode=0,
        stdout="install bundle successfully",
        stderr="",
    )
    run = Mock(return_value=result)
    monkeypatch.setattr(installer, "run_process", run)
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    assert installer.Installer(serial="device-serial").install("app.hap") is True
    assert run.call_args.args[0] == [
        installer.config.HDC_PATH,
        "-t",
        "device-serial",
        "install",
        "-r",
        "app.hap",
    ]


def test_installer_accepts_success_with_error_in_path(monkeypatch) -> None:
    # 路径/包名含 error/failed 但安装成功 → 不得误判
    result = SimpleNamespace(
        returncode=0,
        stdout="[Info]install bundle successfully",
        stderr="",
    )
    monkeypatch.setattr(installer, "run_process", Mock(return_value=result))
    monkeypatch.setattr(installer.Installer, "_ensure_server", lambda self: None)

    hdc = installer.Installer()
    assert hdc.install(r"C:\error\failed-app.hap") is True
    assert hdc.install(r"C:\tmp\debug\failed_error.apk.hap") is True


def test_installer_closes_server_started_by_current_task(monkeypatch) -> None:
    # 无既有 server → hdc start 启动 PID 47024 → close 只 kill 该 PID
    listener_pids = [None, 47024, 47024]
    monkeypatch.setattr(installer, "_listener_pid", lambda: listener_pids.pop(0))
    monkeypatch.setattr(installer, "_process_start_time", lambda pid: time.time())
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)

    hdc = installer.Installer()
    hdc._ensure_server()
    hdc.close()
    hdc.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [[hdc._hdc, "start"], [hdc._hdc, "kill"]]


def test_installer_preserves_preexisting_server(monkeypatch) -> None:
    monkeypatch.setattr(installer, "_listener_pid", lambda: 47024)
    run = Mock()
    monkeypatch.setattr(installer.subprocess, "run", run)

    with installer.Installer() as hdc:
        hdc._ensure_server()

    run.assert_not_called()


def test_installer_does_not_take_over_non_hdc_listener(monkeypatch) -> None:
    # 端口被非 HDC 进程占用：不启动、不接管、close 不清理
    monkeypatch.setattr(installer, "_listener_pid", lambda: 12345)
    run = Mock()
    monkeypatch.setattr(installer.subprocess, "run", run)

    with installer.Installer() as hdc:
        hdc._ensure_server()

    run.assert_not_called()


def test_installer_refuses_external_server_taken_over_before_start(
    monkeypatch,
) -> None:
    # 探测后外部 HDC 抢先启动：监听进程创建早于本次 hdc start，不接管、不清理
    listener_pids = [None, 55555]
    monkeypatch.setattr(installer, "_listener_pid", lambda: listener_pids.pop(0))
    monkeypatch.setattr(installer, "_process_start_time", lambda pid: time.time() - 30)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)

    hdc = installer.Installer()
    hdc._ensure_server()
    hdc.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [[hdc._hdc, "start"]]


def test_installer_start_failure_does_not_kill(monkeypatch) -> None:
    monkeypatch.setattr(installer, "_listener_pid", lambda: None)
    monkeypatch.setattr(installer, "_LISTENER_POLL_ATTEMPTS", 1)
    run = Mock(
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="server error")
    )
    monkeypatch.setattr(installer.subprocess, "run", run)

    hdc = installer.Installer()
    hdc._ensure_server()
    hdc.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [[hdc._hdc, "start"]]


def test_installer_skips_kill_after_server_pid_changes(monkeypatch) -> None:
    # 启动成功后监听 PID 被外部接管：close 只核对不 kill
    listener_pids = [None, 47024, 99999]
    monkeypatch.setattr(installer, "_listener_pid", lambda: listener_pids.pop(0))
    monkeypatch.setattr(installer, "_process_start_time", lambda pid: time.time())
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)

    hdc = installer.Installer()
    hdc._ensure_server()
    hdc.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [[hdc._hdc, "start"]]


def test_installer_start_failure_does_not_own(monkeypatch) -> None:
    # 本任务启动失败：hdc start 后无监听进程，不归属，close 不清理
    listener_pids = [None, None]
    monkeypatch.setattr(
        installer,
        "_listener_pid",
        lambda: listener_pids.pop(0) if listener_pids else None,
    )
    monkeypatch.setattr(installer, "_LISTENER_POLL_ATTEMPTS", 1)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)

    hdc = installer.Installer()
    hdc._ensure_server()
    hdc.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [[hdc._hdc, "start"]]


def test_installer_polls_until_listener_appears(monkeypatch) -> None:
    # hdc start 后监听进程稍晚出现：轮询直到确认 PID 再归属
    listener_pids = [None, None, None, 47024, 47024]
    monkeypatch.setattr(
        installer,
        "_listener_pid",
        lambda: listener_pids.pop(0) if listener_pids else None,
    )
    monkeypatch.setattr(installer, "_LISTENER_POLL_INTERVAL", 0)
    monkeypatch.setattr(installer, "_process_start_time", lambda pid: time.time())
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)

    hdc = installer.Installer()
    hdc._ensure_server()
    hdc.close()

    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [[hdc._hdc, "start"], [hdc._hdc, "kill"]]


def test_listener_pid_parses_netstat_output(monkeypatch) -> None:
    output = (
        "  TCP    127.0.0.1:8710         0.0.0.0:0              LISTENING       47024\n"
        "  TCP    127.0.0.1:8710         127.0.0.1:65140        TIME_WAIT       0\n"
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1234\n"
    )
    monkeypatch.setattr(installer.os, "name", "nt")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout=output, stderr="")),
    )
    assert installer._listener_pid() == 47024


def test_listener_pid_returns_none_when_port_free(monkeypatch) -> None:
    output = (
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1234\n"
    )
    monkeypatch.setattr(installer.os, "name", "nt")
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout=output, stderr="")),
    )
    assert installer._listener_pid() is None


def test_generate_keypair_builds_command(monkeypatch, tmp_path) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(keytool_util, "run_process", run)

    keystore = tmp_path / "key.p12"
    result = keytool_util.KeytoolUtil().generate_keypair(
        str(keystore), "debugKey", "pw"
    )

    command = run.call_args.args[0]
    assert result is True
    assert "-genkeypair" in command
    assert command[command.index("-alias") + 1] == "debugKey"
    assert command[command.index("-storepass") + 1] == "pw"
    assert command[command.index("-keystore") + 1] == str(keystore)


def test_generate_keypair_removes_existing_keystore(monkeypatch, tmp_path) -> None:
    keystore = tmp_path / "key.p12"
    keystore.write_bytes(b"old")
    removed = []
    monkeypatch.setattr(keytool_util.os, "remove", removed.append)
    monkeypatch.setattr(
        keytool_util,
        "run_process",
        Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    keytool_util.KeytoolUtil().generate_keypair(str(keystore))

    assert removed == [str(keystore)]


def test_generate_keypair_reports_failure(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(returncode=1, stdout="", stderr="genkey failed")
    monkeypatch.setattr(keytool_util, "run_process", Mock(return_value=result))

    with pytest.raises(RuntimeError, match="genkeypair"):
        keytool_util.KeytoolUtil().generate_keypair(str(tmp_path / "key.p12"))


def test_generate_csr_reads_file_content(monkeypatch, tmp_path) -> None:
    csr = tmp_path / "req.csr"

    def create_csr(command, **_kwargs):
        output_path = command[command.index("-file") + 1]
        assert output_path == str(csr)
        csr.write_text("csr-content", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    run = Mock(side_effect=create_csr)
    monkeypatch.setattr(keytool_util, "run_process", run)

    content = keytool_util.KeytoolUtil().generate_csr(
        str(tmp_path / "key.p12"), "debugKey", "pw", str(csr)
    )

    command = run.call_args.args[0]
    assert content == "csr-content"
    assert command[command.index("-file") + 1] == str(csr)


def test_generate_csr_reports_failure(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(returncode=1, stdout="", stderr="certreq failed")
    monkeypatch.setattr(keytool_util, "run_process", Mock(return_value=result))

    with pytest.raises(RuntimeError, match="certreq"):
        keytool_util.KeytoolUtil().generate_csr(
            str(tmp_path / "key.p12"), "debugKey", "pw", str(tmp_path / "req.csr")
        )


def test_generate_csr_reports_read_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        keytool_util,
        "run_process",
        Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    with pytest.raises(RuntimeError, match="无法读取"):
        keytool_util.KeytoolUtil().generate_csr(
            str(tmp_path / "key.p12"), "debugKey", "pw", str(tmp_path / "missing.csr")
        )
