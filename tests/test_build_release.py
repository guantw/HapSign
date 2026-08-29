"""Release product matrix and packaging tests."""

import json

import pytest

from scripts import build_release


@pytest.mark.parametrize(
    ("pep440", "release"),
    [
        ("0.2.0rc1", "0.2.0-rc.1"),
        ("1.0.0b2", "1.0.0-beta.2"),
        ("1.0.0", "1.0.0"),
    ],
)
def test_release_version_conversion(pep440, release) -> None:
    assert build_release.release_version(pep440) == release


def test_supported_matrix_explicitly_rejects_macos_x64() -> None:
    assert build_release.supported_products("macos", "arm64") == (
        build_release.EXTERNAL,
    )
    with pytest.raises(RuntimeError, match="explicitly unsupported"):
        build_release.supported_products("macos", "x64")


def test_asset_names_match_release_contract() -> None:
    assert (
        build_release.asset_filename(
            build_release.PORTABLE, "0.2.0-rc.1", "linux", "x64"
        )
        == "HapSign-CLI-Portable-v0.2.0-rc.1-linux-x64.tar.gz"
    )
    assert (
        build_release.asset_filename(build_release.GUI, "0.2.0-rc.1", "windows", "x64")
        == "HapSign-GUI-v0.2.0-rc.1-windows-x64.zip"
    )


def test_build_info_declares_unsigned_product(tmp_path) -> None:
    path = build_release._write_build_info(
        tmp_path,
        product=build_release.GUI,
        version="0.2.0-rc.1",
        platform_name="windows",
        architecture="x64",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["product"] == "HapSign GUI"
    assert payload["release_version"] == "0.2.0-rc.1"
    assert payload["bundled_browser"] is True
    assert payload["bundled_toolchain"] is True
    assert payload["code_signed"] is False
    assert payload["publisher_signed"] is False
    assert payload["macos_adhoc_signature"] is False
    assert payload["notarized"] is False


def test_macos_build_info_discloses_mandatory_adhoc_signature(tmp_path) -> None:
    path = build_release._write_build_info(
        tmp_path,
        product=build_release.EXTERNAL,
        version="0.2.0-rc.1",
        platform_name="macos",
        architecture="arm64",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["code_signed"] is True
    assert payload["publisher_signed"] is False
    assert payload["macos_adhoc_signature"] is True
    assert payload["notarized"] is False


def test_cli_build_uses_onedir_not_onefile(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    captured = {}

    def fake_run(command, *, env) -> None:
        captured["command"] = command
        captured["env"] = env
        dist = project / "build" / "release-cli-dist" / "hapsign-cli"
        dist.mkdir(parents=True)
        (dist / build_release._cli_executable_name()).write_bytes(b"cli")

    monkeypatch.setattr(build_release, "PROJECT_ROOT", project)
    monkeypatch.setattr(build_release, "_run", fake_run)

    root = build_release._build_cli_base(env={"HAPSIGN_BUNDLE_CHROMIUM": "0"})

    assert root.name == "hapsign-cli"
    assert "--onedir" in captured["command"]
    assert "--onefile" not in captured["command"]
    assert captured["env"]["HAPSIGN_BUNDLE_CHROMIUM"] == "0"
