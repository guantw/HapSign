"""跨平台运行目录与便携工具链发现测试。"""

from hapsign import runtime


def test_app_data_dir_honors_override(tmp_path, monkeypatch) -> None:
    target = tmp_path / "state"
    monkeypatch.setenv("HAPSIGN_DATA_DIR", str(target))

    assert runtime.app_data_dir() == target.resolve()


def test_app_data_dir_defaults_to_application_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("HAPSIGN_DATA_DIR", raising=False)
    monkeypatch.setattr(runtime, "application_dir", lambda: tmp_path)

    assert runtime.app_data_dir() == tmp_path


def test_portable_toolchain_takes_precedence(tmp_path, monkeypatch) -> None:
    resources = tmp_path / "resources"
    monkeypatch.setenv("HAPSIGN_RESOURCE_DIR", str(resources))
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    root = resources / "toolchain" / "windows"
    paths = [
        root / "jbr" / "bin" / "java.exe",
        root / "jbr" / "bin" / "keytool.exe",
        root / "lib" / "hap-sign-tool.jar",
        root / "bin" / "hdc.exe",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tool")

    toolchain = runtime.discover_toolchain()

    assert toolchain.source == "portable"
    assert toolchain.missing() == []
    assert toolchain.hdc == paths[-1]


def test_portable_public_runtime_takes_precedence_over_legacy_jbr(
    tmp_path,
    monkeypatch,
) -> None:
    resources = tmp_path / "resources"
    monkeypatch.setenv("HAPSIGN_RESOURCE_DIR", str(resources))
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    root = resources / "toolchain" / "windows"
    for name in ("java.exe", "keytool.exe"):
        for runtime_name in ("runtime", "jbr"):
            path = root / runtime_name / "bin" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(runtime_name.encode())
    for path in (
        root / "lib" / "hap-sign-tool.jar",
        root / "bin" / "hdc.exe",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tool")

    toolchain = runtime.discover_toolchain()

    assert toolchain.java == root / "runtime" / "bin" / "java.exe"
    assert toolchain.keytool == root / "runtime" / "bin" / "keytool.exe"


def test_signed_hap_only_requires_hdc(tmp_path) -> None:
    hdc = tmp_path / "hdc"
    hdc.write_bytes(b"tool")
    toolchain = runtime.ToolchainPaths(
        java=tmp_path / "missing-java",
        keytool=tmp_path / "missing-keytool",
        hap_sign_tool=tmp_path / "missing.jar",
        hdc=hdc,
        source="test",
    )

    assert toolchain.missing(require_signing=False) == []
    assert len(toolchain.missing(require_signing=True)) == 3


def test_platform_tag_normalizes_darwin(monkeypatch) -> None:
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")

    assert runtime.platform_tag() == "macos"
