"""命令行入口测试。"""

import json
import zipfile

import pytest

from hapsign import cli


def _write_hap(path, module_data: dict) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("module.json", json.dumps(module_data))


def test_detect_bundle_name(tmp_path) -> None:
    hap_path = tmp_path / "app.hap"
    _write_hap(hap_path, {"app": {"bundleName": "com.example.app"}})

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


@pytest.mark.parametrize(("pipeline_result", "exit_code"), [(True, 0), (False, 1)])
def test_main_returns_pipeline_result(monkeypatch, pipeline_result, exit_code) -> None:
    captured = {}

    class FakePipeline:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return pipeline_result

    monkeypatch.setattr(cli, "SignPipeline", FakePipeline)

    result = cli.main(["--hap", "example.hap", "--bundle-name", "com.example.app"])

    assert result == exit_code
    assert captured["hap_path"] == "example.hap"
    assert captured["bundle_name"] == "com.example.app"
