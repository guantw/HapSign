"""命令行入口与 Agent JSON 协议测试。"""

import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from hapsign import cli
from hapsign.runtime import ToolchainPaths


def _write_hap(path: Path, module_data: dict | None = None) -> None:
    data = module_data or {"app": {"bundleName": "com.example.app"}}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("module.json", json.dumps(data))


def _write_legacy_metadata(
    path: Path,
    bundle_name: str,
    *,
    enable_capability: bool | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "creation_date": date.today().isoformat(),
        "bundle_name": bundle_name,
        "udid": "A" * 64,
        "p12_path": "key.p12",
        "cer_path": "cert.cer",
        "p7b_path": "profile.p7b",
    }
    if enable_capability is not None:
        metadata["enable_capability"] = enable_capability
    path.write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    for name in ("key.p12", "cert.cer", "profile.p7b"):
        (path.parent / name).write_bytes(b"material")


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


def test_parser_uses_configured_application_state_dir_by_default(
    monkeypatch, tmp_path
) -> None:
    settings = object()
    expected = tmp_path / "signing_files"
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "signing_files_dir", lambda value: expected)
    args = cli.build_parser().parse_args(["auth", "status"])
    assert Path(args.state_dir) == expected


def test_auth_help_describes_configurable_application_state(capsys) -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit, match="0"):
        parser.parse_args(["auth", "--help"])
    help_text = capsys.readouterr().out
    assert "HAPSIGN_SIGNING_DIR" in help_text
    assert "应用配置" in help_text


@pytest.mark.parametrize(("pipeline_result", "exit_code"), [(True, 0), (False, 1)])
def test_deploy_json_returns_pipeline_result(
    monkeypatch, tmp_path, capsys, pipeline_result, exit_code
) -> None:
    hap_path = tmp_path / "example.hap"
    state_dir = tmp_path / "signing-state"
    output_dir = tmp_path / "signed-outputs"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.signed_hap_path = str(hap_path)
            self.last_error = "检测设备连接: device unavailable"
            self.enable_capability = kwargs["enable_capability"]

        def run(self):
            return pipeline_result

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, "signing_files_dir", lambda _settings: state_dir)
    monkeypatch.setattr(cli, "signed_haps_dir", lambda: output_dir)
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
        ["deploy", "--hap", str(hap_path), "--serial", "device-serial", "--json"]
    )
    assert result == exit_code
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is pipeline_result
    assert payload["command"] == "deploy"
    assert captured["hap_path"] == str(hap_path)
    assert captured["bundle_name"] == "com.example.app"
    assert captured["serial"] == "device-serial"
    assert captured["install_after_sign"] is True
    assert captured["browser_mode"] == "auto"
    assert Path(captured["state_dir"]) == state_dir
    assert Path(captured["signed_output_dir"]) == output_dir
    if pipeline_result:
        assert payload["installed"] is True
        assert payload["signed_hap"] == str(hap_path)
        assert payload["requested_capability_mode"] == "normal"
        assert payload["capability_mode"] == "normal"
        assert payload["capability_fallback"] is False
    else:
        assert payload["error"]["message"] == "检测设备连接: device unavailable"


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
            ["sign", "--hap", str(hap_path), "--serial", "device-serial", "--json"]
        )
        == 1
    )
    stdout = capsys.readouterr().out
    assert device_udid not in stdout
    payload = json.loads(stdout)
    assert payload["error"]["message"] == "Device not found in list: <redacted-udid>"


def test_sign_json_does_not_install(monkeypatch, tmp_path, capsys) -> None:
    hap_path = tmp_path / "unsigned.hap"
    signed_path = tmp_path / "unsigned_signed.hap"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.signed_hap_path = str(signed_path)
            self.enable_capability = kwargs["enable_capability"]

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
            ["sign", "--hap", str(hap_path), "--serial", "device-serial", "--json"]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["installed"] is False
    assert payload["signed_hap"] == str(signed_path)
    assert payload["requested_capability_mode"] == "normal"
    assert payload["capability_mode"] == "normal"
    assert payload["capability_fallback"] is False
    assert captured["install_after_sign"] is False


def test_sign_json_reports_real_profile_fallback(monkeypatch, tmp_path, capsys) -> None:
    hap_path = tmp_path / "unsigned.hap"
    signed_path = tmp_path / "unsigned_signed.hap"
    _write_hap(hap_path)

    class FakePipeline:
        signed_hap_path = str(signed_path)
        last_error = ""
        enable_capability = False

        def __init__(self, **_kwargs):
            pass

        def run(self):
            return True

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)

    result = cli.main(["sign", "--hap", str(hap_path), "--enable-capability", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["requested_capability_mode"] == "system-basic"
    assert payload["capability_mode"] == "normal"
    assert payload["capability_fallback"] is True


def test_signed_input_rejects_existing_output_as_invalid_input(
    monkeypatch, tmp_path, capsys
) -> None:
    hap_path = tmp_path / "signed.hap"
    output_path = tmp_path / "existing.hap"
    _write_hap(hap_path)
    output_path.write_bytes(b"existing")
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: True)

    result = cli.main(
        [
            "sign",
            "--hap",
            str(hap_path),
            "--output",
            str(output_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 2
    assert payload["error"]["type"] == "invalid_input"
    assert "输出已存在" in payload["error"]["message"]
    assert output_path.read_bytes() == b"existing"


def test_cli_directory_flags_override_configured_defaults(
    tmp_path, monkeypatch
) -> None:
    hap_path = tmp_path / "example.hap"
    state = tmp_path / "state"
    output = tmp_path / "output"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        signed_hap_path = str(tmp_path / "signed.hap")
        last_error = ""

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.enable_capability = kwargs["enable_capability"]

        def run(self):
            return True

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    result = cli.main(
        [
            "sign",
            "--hap",
            str(hap_path),
            "--state-dir",
            str(state),
            "--output-dir",
            str(output),
            "--json",
        ]
    )
    assert result == 0
    assert Path(captured["state_dir"]) == state
    assert Path(captured["work_dir"]) == state / "com.example.app"
    assert Path(captured["signed_output_dir"]) == output


def test_exact_output_takes_priority_over_output_directory(
    tmp_path, monkeypatch
) -> None:
    hap_path = tmp_path / "example.hap"
    exact_output = tmp_path / "exact" / "signed.hap"
    output_dir = tmp_path / "fallback"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        signed_hap_path = str(exact_output)
        last_error = ""

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.enable_capability = kwargs["enable_capability"]

        def run(self):
            return True

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    assert (
        cli.main(
            [
                "sign",
                "--hap",
                str(hap_path),
                "--output-dir",
                str(output_dir),
                "--output",
                str(exact_output),
                "--json",
            ]
        )
        == 0
    )
    assert captured["signed_output_path"] == str(exact_output)
    assert Path(captured["signed_output_dir"]) == output_dir


def test_existing_exact_output_fails_before_pipeline_runs(
    tmp_path, monkeypatch, capsys
) -> None:
    hap_path = tmp_path / "example.hap"
    output = tmp_path / "signed.hap"
    _write_hap(hap_path)
    output.write_bytes(b"existing")
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    monkeypatch.setattr(
        cli,
        "SignPipeline",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline ran")),
    )

    result = cli.main(
        [
            "sign",
            "--hap",
            str(hap_path),
            "--output",
            str(output),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["error"]["type"] == "invalid_input"
    assert output.read_bytes() == b"existing"


def test_cli_browser_default_allows_valid_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("HAPSIGN_BROWSER", "system")
    assert cli.build_parser().parse_args(["auth"]).browser == "system"
    monkeypatch.setenv("HAPSIGN_BROWSER", "invalid")
    assert cli.build_parser().parse_args(["auth"]).browser == "auto"


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--callback-port", "not-a-port"),
        ("--callback-port", "-1"),
        ("--callback-port", "65536"),
        ("--auth-timeout", "not-a-timeout"),
        ("--auth-timeout", "29"),
        ("--auth-timeout", "3601"),
    ],
)
def test_auth_interaction_options_reject_invalid_values(option, value, capsys) -> None:
    result = cli.main(["auth", option, value, "--json"])

    assert result == cli.EXIT_USAGE
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_arguments"


def test_auth_interaction_options_accept_boundary_values() -> None:
    low = cli.build_parser().parse_args(
        ["auth", "--callback-port", "0", "--auth-timeout", "30"]
    )
    high = cli.build_parser().parse_args(
        ["auth", "--callback-port", "65535", "--auth-timeout", "3600"]
    )

    assert (low.callback_port, low.auth_timeout) == (0, 30)
    assert (high.callback_port, high.auth_timeout) == (65535, 3600)


def test_cli_auth_interaction_options_are_forwarded(
    monkeypatch, tmp_path, capsys
) -> None:
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def authenticate(self, force_refresh=False):
            captured["force_refresh"] = force_refresh
            captured["auth_event_callback"](
                {
                    "event": "auth_required",
                    "method": "ssh_loopback",
                    "reason": "ssh",
                    "verification_uri": "https://example.invalid/one-time",
                    "callback_host": "127.0.0.1",
                    "callback_port": 43123,
                    "expires_in": 900,
                }
            )
            return {"authenticated": True, "from_cache": False}

        def auth_status(self):
            return {
                "authenticated": True,
                "cache_path": tmp_path / ".token_cache.json",
            }

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)

    result = cli.main(
        [
            "auth",
            "--browser",
            "external",
            "--callback-port",
            "43123",
            "--auth-timeout",
            "900",
            "--events",
            "json",
            "--state-dir",
            str(tmp_path),
            "--json",
        ]
    )

    assert result == 0
    assert captured["browser_mode"] == "external"
    assert captured["callback_port"] == 43123
    assert captured["auth_timeout"] == 900
    assert callable(captured["auth_event_callback"])
    output = capsys.readouterr()
    assert json.loads(output.out)["ok"] is True
    event_line = next(
        line for line in output.err.splitlines() if line.startswith("HAPSIGN_EVENT=")
    )
    event = json.loads(event_line.removeprefix("HAPSIGN_EVENT="))
    assert event["verification_uri"] == "https://example.invalid/one-time"


def test_json_auth_event_has_stable_prefix(capsys) -> None:
    args = cli.build_parser().parse_args(["auth", "--events", "json"])
    callback = cli._auth_event_callback(args)
    callback(
        {
            "event": "auth_required",
            "method": "ssh_loopback",
            "reason": "ssh",
            "verification_uri": "https://example.invalid/one-time",
            "callback_host": "127.0.0.1",
            "callback_port": 43123,
            "expires_in": 600,
        }
    )

    stderr = capsys.readouterr().err.strip()
    assert stderr.startswith("HAPSIGN_EVENT=")
    event = json.loads(stderr.removeprefix("HAPSIGN_EVENT="))
    assert event["event"] == "auth_required"
    assert event["callback_port"] == 43123


def test_headless_auth_event_explains_loopback_forwarding(capsys) -> None:
    args = cli.build_parser().parse_args(["auth"])
    callback = cli._auth_event_callback(args)
    callback(
        {
            "event": "auth_required",
            "method": "loopback_forwarding",
            "reason": "headless",
            "verification_uri": "https://example.invalid/one-time",
            "callback_host": "127.0.0.1",
            "callback_port": 43123,
            "expires_in": 600,
        }
    )

    stderr = capsys.readouterr().err
    assert "ssh -N -L 127.0.0.1:43123:127.0.0.1:43123" in stderr
    assert "不要公网暴露端口" in stderr
    assert "https://example.invalid/one-time" in stderr


def test_sign_rejects_serial_and_device_udid_together(capsys) -> None:
    result = cli.main(
        [
            "sign",
            "--hap",
            "app.hap",
            "--serial",
            "device-serial",
            "--device-udid",
            "A" * 64,
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 2
    assert payload["error"]["type"] == "invalid_arguments"
    assert "not allowed" in payload["error"]["message"]


def test_sign_accepts_explicit_udid_and_prints_json(
    tmp_path, monkeypatch, capsys
) -> None:
    hap_path = tmp_path / "example.hap"
    exact_output = tmp_path / "artifacts" / "signed.hap"
    _write_hap(hap_path)
    captured = {}

    class FakePipeline:
        signed_hap_path = str(exact_output)
        last_error = ""

        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.enable_capability = kwargs["enable_capability"]

        def run(self):
            return True

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    result = cli.main(
        [
            "sign",
            "--hap",
            str(hap_path),
            "--device-udid",
            "A" * 64,
            "--output",
            str(exact_output),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["ok"] is True
    assert payload["command"] == "sign"
    assert payload["installed"] is False
    assert payload["browser_mode"] == "auto"
    assert payload["migration_warnings"] == []
    assert Path(payload["signed_hap"]).is_absolute()
    assert captured["install_after_sign"] is False
    assert captured["device_udid"] == "A" * 64
    assert captured["signed_output_path"] == str(exact_output)


def test_inspect_json_does_not_run_pipeline(tmp_path, monkeypatch, capsys) -> None:
    hap_path = tmp_path / "app.hap"
    _write_hap(hap_path, {"app": {"bundleName": "com.example.inspect"}})
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: True)
    monkeypatch.setattr(
        cli,
        "SignPipeline",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline ran")),
    )
    result = cli.main(["inspect", "--hap", str(hap_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["command"] == "inspect"
    assert payload["bundle_name"] == "com.example.inspect"
    assert payload["signed"] is True
    assert payload["migration_warnings"] == []


def test_inspect_reports_applicable_destructive_cache_migration(
    tmp_path, monkeypatch, capsys
) -> None:
    hap_path = tmp_path / "app.hap"
    state = tmp_path / "state"
    metadata = state / "com.example.inspect" / "metadata.json"
    _write_hap(hap_path, {"app": {"bundleName": "com.example.inspect"}})
    _write_legacy_metadata(metadata, "com.example.inspect")
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)
    result = cli.main(
        [
            "inspect",
            "--hap",
            str(hap_path),
            "--state-dir",
            str(state),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["migration_warnings"][0]["id"] == "HAPSIGN-BREAKING-001"
    assert payload["migration_warnings"][0]["destructive"] is True


def test_inspect_evaluates_the_requested_capability_mode(
    tmp_path, monkeypatch, capsys
) -> None:
    hap_path = tmp_path / "app.hap"
    state = tmp_path / "state"
    metadata = state / "com.example.inspect" / "metadata.json"
    _write_hap(hap_path, {"app": {"bundleName": "com.example.inspect"}})
    _write_legacy_metadata(
        metadata,
        "com.example.inspect",
        enable_capability=True,
    )
    monkeypatch.setattr(cli, "is_hap_signed", lambda _path: False)

    result = cli.main(
        ["inspect", "--hap", str(hap_path), "--state-dir", str(state), "--json"]
    )
    normal_payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert normal_payload["migration_warnings"][0]["reasons"] == [
        "capability_mode_mismatch"
    ]

    result = cli.main(
        [
            "inspect",
            "--hap",
            str(hap_path),
            "--state-dir",
            str(state),
            "--enable-capability",
            "--json",
        ]
    )
    capability_payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert capability_payload["migration_warnings"] == []


def test_migrate_legacy_cache_command_does_not_run_pipeline(
    tmp_path, monkeypatch, capsys
) -> None:
    hap_path = tmp_path / "app.hap"
    state = tmp_path / "state"
    metadata = state / "com.example.app" / "metadata.json"
    _write_hap(hap_path)
    _write_legacy_metadata(metadata, "com.example.app")
    monkeypatch.setattr(
        cli,
        "SignPipeline",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("pipeline ran")),
    )
    result = cli.main(
        [
            "migrate-cache",
            "--hap",
            str(hap_path),
            "--state-dir",
            str(state),
            "--profile-type",
            "normal",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["command"] == "migrate-cache"
    assert payload["changed"] is True
    assert payload["enable_capability"] is False


def test_doctor_report_separates_signing_and_device_capabilities(tmp_path) -> None:
    java = tmp_path / "java"
    keytool = tmp_path / "keytool"
    signer = tmp_path / "hap-sign-tool.jar"
    for path in (java, keytool):
        path.write_bytes(b"tool")
        path.chmod(path.stat().st_mode | 0o111)
    signer.write_bytes(b"signer")
    toolchain = ToolchainPaths(
        java=java,
        keytool=keytool,
        hap_sign_tool=signer,
        hdc=tmp_path / "missing-hdc",
        source="test",
    )
    report = cli.doctor_report(toolchain)
    assert report["ok"] is False
    assert report["capabilities"]["signing"]["ok"] is True
    assert report["capabilities"]["device"]["ok"] is False
    assert report["tools"]["hap_sign_tool"]["exists"] is True
    assert report["paths"]["state_dir"]
    assert report["breaking_changes"][0]["id"].startswith("HAPSIGN-BREAKING-")


def test_doctor_json_uses_exit_code_for_incomplete_toolchain(
    monkeypatch, capsys
) -> None:
    report = {
        "ok": False,
        "command": "doctor",
        "platform": "linux",
        "architecture": "x86_64",
        "python": "3.13",
        "toolchain_source": "test",
        "capabilities": {},
        "tools": {},
    }
    monkeypatch.setattr(cli, "doctor_report", lambda **_kwargs: report)
    result = cli.main(["doctor", "--json"])
    report["message"] = "环境诊断完成"
    assert result == 1
    assert json.loads(capsys.readouterr().out) == report


def test_invalid_udid_returns_machine_readable_error(tmp_path, capsys) -> None:
    hap_path = tmp_path / "app.hap"
    _write_hap(hap_path)
    result = cli.main(
        ["sign", "--hap", str(hap_path), "--device-udid", "invalid", "--json"]
    )
    output = capsys.readouterr().out
    assert output.isascii()
    payload = json.loads(output)
    assert result == 2
    assert payload["ok"] is False
    assert payload["command"] == "sign"
    assert payload["error"]["type"] == "invalid_input"
    assert "64 位" in payload["error"]["message"]


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
            return {"authenticated": True, "cache_path": str(cache_path)}

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    assert cli.main(["auth", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["from_cache"] is True


def test_auth_login_fails_when_cache_was_not_persisted(
    monkeypatch, tmp_path, capsys
) -> None:
    cache_path = tmp_path / ".token_cache.json"

    class FakePipeline:
        def __init__(self, **_kwargs):
            pass

        def authenticate(self, force_refresh=False):
            return {
                "authenticated": True,
                "from_cache": False,
                "creation_date": "2026-08-28",
            }

        def auth_status(self):
            return {"authenticated": False, "cache_path": str(cache_path)}

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)
    assert cli.main(["auth", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "operation_failed"
    assert "Token 缓存" in payload["error"]["message"]


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
    assert cli.main(["deploy", "--hap", "app.hap", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "deploy"
    assert payload["error"]["type"] == "invalid_arguments"
    assert "--serial" in payload["error"]["message"]


def test_legacy_hap_invocation_is_rejected(capsys) -> None:
    assert cli.main(["--hap", "app.hap", "--serial", "device-serial", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "unknown"
    assert payload["error"]["type"] == "invalid_arguments"


@pytest.mark.parametrize("command", ["sign", "install", "deploy"])
def test_empty_serial_is_rejected_as_invalid_arguments(command, capsys) -> None:
    assert cli.main([command, "--hap", "app.hap", "--serial", " ", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == command
    assert payload["error"]["type"] == "invalid_arguments"
    assert "--serial" in payload["error"]["message"]


def test_missing_hap_json_is_invalid_input(capsys, tmp_path) -> None:
    missing = tmp_path / "missing.hap"
    assert cli.main(["sign", "--hap", str(missing), "--json"]) == 2
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
