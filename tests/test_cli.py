"""命令行入口与 Agent JSON 协议测试。"""

import json
import zipfile

import pytest

from hapsign import cli


def _write_hap(path, module_data: dict | None = None) -> None:
    data = module_data or {"app": {"bundleName": "com.example.app"}}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("module.json", json.dumps(data))


def test_detect_bundle_name(tmp_path) -> None:
    hap_path = tmp_path / "app.hap"
    _write_hap(hap_path)

    assert cli.detect_bundle_name(str(hap_path)) == "com.example.app"


def test_detect_bundle_name_requires_module_json(tmp_path) -> None:
    hap_path = tmp_path / "app.hap"
    with zipfile.ZipFile(hap_path, "w") as archive:
        archive.writestr("other.json", "{}")

    with pytest.raises(ValueError, match="module.json"):
        cli.detect_bundle_name(str(hap_path))


def test_detect_bundle_name_requires_bundle_name(tmp_path) -> None:
    hap_path = tmp_path / "app.hap"
    _write_hap(hap_path, {"app": {}})

    with pytest.raises(ValueError, match="app.bundleName"):
        cli.detect_bundle_name(str(hap_path))


def test_legacy_hap_invocation_maps_to_deploy() -> None:
    assert cli._normalize_legacy_args(
        ["--hap", "app.hap", "--serial", "device-serial"]
    ) == ["deploy", "--hap", "app.hap", "--serial", "device-serial"]


def test_parser_uses_home_state_dir_by_default(monkeypatch, tmp_path) -> None:
    expected = str(tmp_path / ".hapsign")
    monkeypatch.setattr(cli, "default_state_dir", lambda: expected)

    args = cli.build_parser().parse_args(["auth", "status"])

    assert args.state_dir == expected


def test_auth_help_renders_windows_home_example(capsys) -> None:
    parser = cli.build_parser()

    with pytest.raises(SystemExit, match="0"):
        parser.parse_args(["auth", "--help"])

    help_text = capsys.readouterr().out
    assert "%USERPROFILE%\\.hapsign" in help_text


@pytest.mark.parametrize(("pipeline_result", "exit_code"), [(True, 0), (False, 1)])
def test_deploy_json_returns_pipeline_result(
    monkeypatch, tmp_path, capsys, pipeline_result, exit_code
) -> None:
    hap_path = tmp_path / "example.hap"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.signed_hap_path = str(hap_path)
            self.last_error = "检测设备连接: device unavailable"

        def run(self):
            return pipeline_result

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    monkeypatch.setattr(
        cli,
        "_inspect_installed_bundle",
        lambda *_args: {
            "bundle_name": "com.example.app",
            "provision_type": "debug",
            "version_name": "1.0.0",
        },
    )

    result = cli.main(
        [
            "deploy",
            "--hap",
            str(hap_path),
            "--serial",
            "device-serial",
            "--json",
        ]
    )

    assert result == exit_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is pipeline_result
    assert payload["command"] == "deploy"
    assert captured["hap_path"] == str(hap_path)
    assert captured["bundle_name"] == "com.example.app"
    assert captured["serial"] == "device-serial"
    assert captured["install_after_sign"] is True
    if pipeline_result:
        assert payload["installed"] is True
        assert payload["signed_hap"] == str(hap_path)
    else:
        assert payload["error"]["message"] == ("检测设备连接: device unavailable")


def test_failure_json_redacts_pipeline_device_udid(
    monkeypatch, tmp_path, capsys
) -> None:
    hap_path = tmp_path / "example.hap"
    _write_hap(hap_path)
    device_udid = "A" * 64

    class FakePipeline:
        def __init__(self, **_kwargs):
            self.last_error = f"Device not found in list: {device_udid}"

        def run(self):
            return False

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)

    assert (
        cli.main(
            [
                "sign",
                "--hap",
                str(hap_path),
                "--serial",
                "device-serial",
                "--json",
            ]
        )
        == 1
    )

    stdout = capsys.readouterr().out
    assert device_udid not in stdout
    payload = json.loads(stdout)
    assert payload["error"]["message"] == ("Device not found in list: <redacted-udid>")


def test_sign_json_does_not_install(monkeypatch, tmp_path, capsys) -> None:
    hap_path = tmp_path / "unsigned.hap"
    signed_path = tmp_path / "unsigned_signed.hap"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.signed_hap_path = str(signed_path)

        def run(self):
            return True

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    monkeypatch.setattr(
        cli,
        "_inspect_installed_bundle",
        lambda *_args: pytest.fail("sign 不应检查已安装 bundle"),
    )

    assert (
        cli.main(
            [
                "sign",
                "--hap",
                str(hap_path),
                "--serial",
                "device-serial",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["installed"] is False
    assert payload["signed_hap"] == str(signed_path)
    assert captured["install_after_sign"] is False


def test_auth_status_json_never_emits_token(monkeypatch, tmp_path, capsys) -> None:
    cache_path = tmp_path / ".token_cache.json"
    cache_path.write_text("{}", encoding="utf-8")

    class FakePipeline:
        def __init__(self, **_kwargs):
            pass

        def auth_status(self):
            return {
                "authenticated": True,
                "cached": True,
                "creation_date": "2026-08-28",
                "cache_path": str(cache_path),
                "online_verified": False,
            }

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)

    assert cli.main(["auth", "status", "--json"]) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["authenticated"] is True
    assert payload["online_verified"] is False
    assert "access_token" not in output
    assert "refresh_token" not in output
    assert "jwt_token" not in output


def test_auth_login_reports_cache_reuse(monkeypatch, tmp_path, capsys) -> None:
    cache_path = tmp_path / ".token_cache.json"
    cache_path.write_text("{}", encoding="utf-8")

    class FakePipeline:
        def __init__(self, **_kwargs):
            pass

        def authenticate(self, force_refresh=False):
            assert force_refresh is False
            return {
                "authenticated": True,
                "from_cache": True,
                "creation_date": "2026-08-28",
            }

        def auth_status(self):
            return {"cache_path": str(cache_path)}

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)

    assert cli.main(["auth", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["from_cache"] is True


def test_devices_list_json(monkeypatch, capsys) -> None:
    targets = [
        {
            "serial": "device-serial",
            "transport": "USB",
            "status": "Connected",
            "host": "localhost",
            "connected": True,
            "usb": True,
            "localhost": False,
            "physical_candidate": True,
            "likely_emulator": False,
        }
    ]

    class FakeInstaller:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_targets(self, connected_only=False):
            assert connected_only is True
            return targets

    monkeypatch.setattr(cli, "Installer", FakeInstaller)

    assert cli.main(["devices", "list", "--connected-only", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["count"] == 1
    assert payload["connected_count"] == 1
    assert payload["targets"] == targets


def test_devices_missing_hdc_is_operation_failure(monkeypatch, capsys) -> None:
    class MissingHdcInstaller:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_targets(self, connected_only=False):
            raise FileNotFoundError(2, "No such file or directory", "hdc")

    monkeypatch.setattr(cli, "Installer", MissingHdcInstaller)

    assert cli.main(["devices", "list", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "devices"
    assert payload["error"]["type"] == "operation_failed"
    assert "hdc" in payload["error"]["message"]


def test_json_argument_error_has_stable_exit_code(capsys) -> None:
    assert cli.main(["sign", "--hap", "app.hap", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "sign"
    assert payload["error"]["type"] == "invalid_arguments"
    assert "--serial" in payload["error"]["message"]


def test_missing_hap_json_is_invalid_input(capsys, tmp_path) -> None:
    missing = tmp_path / "missing.hap"

    assert (
        cli.main(
            [
                "sign",
                "--hap",
                str(missing),
                "--serial",
                "device-serial",
                "--json",
            ]
        )
        == 2
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_input"


def test_install_signed_hap_with_explicit_serial(monkeypatch, tmp_path, capsys) -> None:
    hap_path = tmp_path / "signed.hap"
    _write_hap(hap_path)
    calls = []

    class FakeInstaller:
        def __init__(self, serial=None):
            calls.append(("serial", serial))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def install(self, path):
            calls.append(("install", path))

        def inspect_bundle(self, bundle_name):
            calls.append(("inspect", bundle_name))
            return {
                "bundle_name": bundle_name,
                "provision_type": "debug",
                "version_name": "1.0.0",
            }

    monkeypatch.setattr(cli, "Installer", FakeInstaller)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: True)

    assert (
        cli.main(
            [
                "install",
                "--hap",
                str(hap_path),
                "--serial",
                "device-serial",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["installed"] is True
    assert calls == [
        ("serial", "device-serial"),
        ("install", str(hap_path)),
        ("inspect", "com.example.app"),
    ]


def test_install_missing_hdc_is_operation_failure(
    monkeypatch, tmp_path, capsys
) -> None:
    hap_path = tmp_path / "signed.hap"
    _write_hap(hap_path)

    class MissingHdcInstaller:
        def __init__(self, serial=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def install(self, path):
            raise FileNotFoundError(2, "No such file or directory", "hdc")

    monkeypatch.setattr(cli, "Installer", MissingHdcInstaller)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: True)

    assert (
        cli.main(
            [
                "install",
                "--hap",
                str(hap_path),
                "--serial",
                "device-serial",
                "--json",
            ]
        )
        == 1
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "install"
    assert payload["error"]["type"] == "operation_failed"
    assert "hdc" in payload["error"]["message"]


def test_install_rejects_unsigned_hap(monkeypatch, tmp_path, capsys) -> None:
    hap_path = tmp_path / "unsigned.hap"
    _write_hap(hap_path)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)

    assert (
        cli.main(
            [
                "install",
                "--hap",
                str(hap_path),
                "--serial",
                "device-serial",
                "--json",
            ]
        )
        == 2
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["type"] == "invalid_input"
    assert "deploy" in payload["error"]["message"]
