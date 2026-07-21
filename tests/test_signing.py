"""外部签名与安装命令的无设备测试。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hapsign.signing import hap_signer, installer, keytool_util


def test_hap_signer_builds_subprocess_command(monkeypatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(hap_signer.subprocess, "run", run)

    result = hap_signer.HapSigner().sign_hap(
        "in.hap", "cert.cer", "profile.p7b", "key.p12", output_path="out.hap"
    )

    command = run.call_args.args[0]
    assert result is True
    assert command[command.index("-inFile") + 1] == "in.hap"
    assert command[command.index("-outFile") + 1] == "out.hap"


def test_hap_signer_reports_tool_failure(monkeypatch) -> None:
    result = SimpleNamespace(returncode=2, stdout="", stderr="sign failed")
    monkeypatch.setattr(hap_signer.subprocess, "run", Mock(return_value=result))

    with pytest.raises(RuntimeError, match="sign failed"):
        hap_signer.HapSigner().sign_hap("in.hap", "cert.cer", "profile.p7b", "key.p12")


def test_installer_extracts_udid(monkeypatch) -> None:
    udid = "A" * 64
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout=udid, stderr=""))
    monkeypatch.setattr(installer.subprocess, "run", run)

    assert installer.Installer().get_udid() == udid


def test_installer_detects_failure_text(monkeypatch) -> None:
    result = SimpleNamespace(returncode=0, stdout="Failure: failed", stderr="")
    monkeypatch.setattr(installer.subprocess, "run", Mock(return_value=result))

    with pytest.raises(RuntimeError, match="hdc install"):
        installer.Installer().install("app.hap")


def test_generate_keypair_builds_command(monkeypatch, tmp_path) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(keytool_util.subprocess, "run", run)

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
        keytool_util.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    keytool_util.KeytoolUtil().generate_keypair(str(keystore))

    assert removed == [str(keystore)]


def test_generate_keypair_reports_failure(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(returncode=1, stdout="", stderr="genkey failed")
    monkeypatch.setattr(keytool_util.subprocess, "run", Mock(return_value=result))

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
    monkeypatch.setattr(keytool_util.subprocess, "run", run)

    content = keytool_util.KeytoolUtil().generate_csr(
        str(tmp_path / "key.p12"), "debugKey", "pw", str(csr)
    )

    command = run.call_args.args[0]
    assert content == "csr-content"
    assert command[command.index("-file") + 1] == str(csr)


def test_generate_csr_reports_failure(monkeypatch, tmp_path) -> None:
    result = SimpleNamespace(returncode=1, stdout="", stderr="certreq failed")
    monkeypatch.setattr(keytool_util.subprocess, "run", Mock(return_value=result))

    with pytest.raises(RuntimeError, match="certreq"):
        keytool_util.KeytoolUtil().generate_csr(
            str(tmp_path / "key.p12"), "debugKey", "pw", str(tmp_path / "req.csr")
        )


def test_generate_csr_reports_read_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        keytool_util.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
    )

    with pytest.raises(RuntimeError, match="无法读取"):
        keytool_util.KeytoolUtil().generate_csr(
            str(tmp_path / "key.p12"), "debugKey", "pw", str(tmp_path / "missing.csr")
        )
