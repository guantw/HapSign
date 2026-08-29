"""跨平台运行目录与便携工具链发现测试。"""

import os

import pytest

from hapsign import runtime


def _write_tool(path, content: bytes = b"tool") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(path.stat().st_mode | 0o111)


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
        _write_tool(path)

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
            _write_tool(path, runtime_name.encode())
    for path in (
        root / "lib" / "hap-sign-tool.jar",
        root / "bin" / "hdc.exe",
    ):
        _write_tool(path)

    toolchain = runtime.discover_toolchain()

    assert toolchain.java == root / "runtime" / "bin" / "java.exe"
    assert toolchain.keytool == root / "runtime" / "bin" / "keytool.exe"


def test_signed_hap_only_requires_hdc(tmp_path) -> None:
    hdc = tmp_path / "hdc"
    _write_tool(hdc)
    toolchain = runtime.ToolchainPaths(
        java=tmp_path / "missing-java",
        keytool=tmp_path / "missing-keytool",
        hap_sign_tool=tmp_path / "missing.jar",
        hdc=hdc,
        source="test",
    )

    assert toolchain.missing(require_signing=False) == []
    assert len(toolchain.missing(require_signing=True)) == 3
    assert toolchain.missing(require_signing=True, require_hdc=False) == [
        f"Java: {tmp_path / 'missing-java'}",
        f"keytool: {tmp_path / 'missing-keytool'}",
        f"hap-sign-tool.jar: {tmp_path / 'missing.jar'}",
    ]


def test_platform_tag_normalizes_darwin(monkeypatch) -> None:
    monkeypatch.setattr(runtime.platform, "system", lambda: "Darwin")

    assert runtime.platform_tag() == "macos"


def test_linux_discovers_java_home_path_and_explicit_signer(
    tmp_path,
    monkeypatch,
) -> None:
    resources = tmp_path / "empty-resources"
    java_home = tmp_path / "jdk"
    path_bin = tmp_path / "path-bin"
    java = java_home / "bin" / "java"
    keytool = java_home / "bin" / "keytool"
    hdc = path_bin / "hdc"
    signer = tmp_path / "sdk" / "lib" / "hap-sign-tool.jar"
    for path in (java, keytool, hdc):
        _write_tool(path)
    signer.parent.mkdir(parents=True)
    signer.write_bytes(b"signer")

    monkeypatch.setattr(runtime.platform, "system", lambda: "Linux")
    monkeypatch.setenv("HAPSIGN_RESOURCE_DIR", str(resources))
    monkeypatch.setenv("JAVA_HOME", str(java_home))
    monkeypatch.setenv("PATH", str(path_bin))
    monkeypatch.setenv("HAPSIGN_HAP_SIGN_TOOL", str(signer))
    monkeypatch.setattr(
        runtime.shutil,
        "which",
        lambda name: str(hdc) if name == "hdc" else None,
    )
    for name in ("HAPSIGN_JAVA", "HAPSIGN_KEYTOOL", "HAPSIGN_HDC", "DEVECO_HOME"):
        monkeypatch.delenv(name, raising=False)

    toolchain = runtime.discover_toolchain()

    assert toolchain.missing() == []
    assert toolchain.java == java
    assert toolchain.keytool == keytool
    assert toolchain.hdc == hdc
    assert toolchain.hap_sign_tool == signer
    assert toolchain.source == "environment overrides"


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable bits only")
def test_posix_non_executable_tool_is_reported(tmp_path) -> None:
    java = tmp_path / "java"
    java.write_bytes(b"java")
    toolchain = runtime.ToolchainPaths(
        java=java,
        keytool=tmp_path / "unused-keytool",
        hap_sign_tool=tmp_path / "unused.jar",
        hdc=tmp_path / "unused-hdc",
        source="test",
    )
    assert (
        "文件不可执行"
        in toolchain.missing(
            require_signing=True,
            require_hdc=False,
        )[0]
    )
