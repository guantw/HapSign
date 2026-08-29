"""GitHub Release assembly gate tests."""

import json

import pytest

from scripts import prepare_release


def _write_matrix(root, version: str) -> None:
    for index, name in enumerate(prepare_release.expected_product_names(version)):
        path = root / f"job-{index}" / name
        path.parent.mkdir(parents=True)
        path.write_bytes(f"asset-{index}".encode())


def test_assemble_release_requires_complete_matrix_and_renders_prompts(
    tmp_path, monkeypatch
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    _write_matrix(input_dir, "0.2.0-rc.1")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)

    paths = prepare_release.assemble_release(
        tag="v0.2.0-rc.1",
        input_dir=input_dir,
        output_dir=output_dir,
    )

    assert len(paths) == 10
    manifest = json.loads(
        (output_dir / "release-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["prerelease"] is True
    assert manifest["official_binaries_publisher_signed"] is False
    assert manifest["notarized"] is False
    assert manifest["support"]["macos"]["x64"] == "unsupported"
    assert len(manifest["assets"]) == 8
    macos = next(item for item in manifest["assets"] if "-macos-arm64" in item["name"])
    other_binaries = [
        item
        for item in manifest["assets"]
        if not item["name"].endswith(".md") and item is not macos
    ]
    assert all(item["code_signed"] is False for item in other_binaries)
    assert macos["code_signed"] is True
    assert macos["publisher_signed"] is False
    assert macos["macos_adhoc_signature"] is True
    prompt = output_dir / "HapSign-Prompt-Portable-v0.2.0-rc.1.md"
    assert "__HAPSIGN_RELEASE_TAG__" not in prompt.read_text(encoding="utf-8")
    assert "v0.2.0-rc.1" in prompt.read_text(encoding="utf-8")
    checksums = (output_dir / "SHA256SUMS").read_text(encoding="ascii")
    assert "release-manifest.json" in checksums
    assert "SHA256SUMS" not in checksums


def test_assemble_release_fails_when_one_product_is_missing(tmp_path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    with pytest.raises(RuntimeError, match="Missing release artifact"):
        prepare_release.assemble_release(
            tag="v0.2.0-rc.1",
            input_dir=input_dir,
            output_dir=output_dir,
        )


@pytest.mark.parametrize("tag", ["0.2.0", "v0.2", "v0.2.0rc1", "latest"])
def test_release_tag_is_strict(tag) -> None:
    with pytest.raises(RuntimeError, match="Unsupported release tag"):
        prepare_release.version_from_tag(tag)
