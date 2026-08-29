"""Machine-readable build metadata tests."""

import json

from hapsign import __version__, build_info


def test_architecture_tag_normalizes_release_names(monkeypatch) -> None:
    monkeypatch.setattr(build_info.platform, "machine", lambda: "AMD64")
    assert build_info.architecture_tag() == "x64"
    monkeypatch.setattr(build_info.platform, "machine", lambda: "aarch64")
    assert build_info.architecture_tag() == "arm64"


def test_build_info_reads_edition_but_keeps_protocol_and_version(
    tmp_path, monkeypatch
) -> None:
    metadata = tmp_path / "BUILD_INFO.json"
    metadata.write_text(
        json.dumps(
            {
                "schema": 999,
                "version": "forged",
                "release_version": "0.2.0-rc.1",
                "cli_protocol": 999,
                "product": "HapSign CLI Portable",
                "edition": "portable",
                "platform": "windows",
                "architecture": "x64",
                "bundled_python": True,
                "bundled_toolchain": True,
                "bundled_browser": False,
                "code_signed": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_info, "build_info_path", lambda: metadata)

    result = build_info.build_info()

    assert result["edition"] == "portable"
    assert result["schema"] == build_info.BUILD_INFO_SCHEMA
    assert result["version"] == __version__
    assert result["release_version"] == "0.2.0-rc.1"
    assert result["product"] == "HapSign CLI Portable"
    assert result["cli_protocol"] == build_info.CLI_PROTOCOL_VERSION
    assert result["code_signed"] is False


def test_invalid_edition_falls_back_to_runtime_inference(tmp_path, monkeypatch) -> None:
    metadata = tmp_path / "BUILD_INFO.json"
    metadata.write_text('{"edition":"unknown"}', encoding="utf-8")
    monkeypatch.setattr(build_info, "build_info_path", lambda: metadata)
    monkeypatch.setattr(build_info, "_inferred_edition", lambda: "source")

    assert build_info.build_info()["edition"] == "source"
