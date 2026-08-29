"""便携版保守精简规则测试。"""

import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hapsign.runtime import ToolchainPaths
from scripts import build_portable


def test_write_sha256_file_uses_standard_sidecar_format(tmp_path) -> None:
    archive = tmp_path / "HapSign-portable-windows.zip"
    archive.write_bytes(b"portable archive")

    checksum = build_portable._write_sha256_file(archive)

    expected = hashlib.sha256(b"portable archive").hexdigest()
    assert checksum.name == "HapSign-portable-windows.zip.sha256"
    assert checksum.read_text(encoding="ascii") == f"{expected}  {archive.name}\n"


def test_linux_portable_archive_uses_tar_to_preserve_executable_bits() -> None:
    assert build_portable._portable_archive_settings("linux") == (
        ".tar.gz",
        "gztar",
    )
    assert build_portable._portable_archive_settings("windows") == (".zip", "zip")
    assert build_portable._portable_archive_settings("macos") == (".zip", "zip")


def test_build_cli_executable_adds_console_program_to_portable(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    portable = tmp_path / "HapSign"
    portable.mkdir()

    def fake_run(command, *, env) -> None:
        assert env["TEST_ENV"] == "1"
        output = command[command.index("--distpath") + 1]
        name = command[command.index("--name") + 1]
        destination = build_portable.Path(output)
        destination.mkdir(parents=True)
        suffix = ".exe" if build_portable.sys.platform == "win32" else ""
        (destination / f"{name}{suffix}").write_bytes(b"cli")

    monkeypatch.setattr(build_portable, "PROJECT_ROOT", project)
    monkeypatch.setattr(build_portable, "_run", fake_run)

    result = build_portable._build_cli_executable(
        portable,
        env={"TEST_ENV": "1"},
    )

    assert result == portable / build_portable._cli_executable_name()
    assert result.read_bytes() == b"cli"


def test_frozen_cli_smoke_checks_version_and_doctor(tmp_path, monkeypatch) -> None:
    executable = tmp_path / build_portable._cli_executable_name()
    executable.write_bytes(b"cli")
    run = Mock(
        side_effect=[
            SimpleNamespace(returncode=0, stdout="hapsign 0.1.0\n", stderr=""),
            SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr=""),
        ]
    )
    monkeypatch.setattr(build_portable.subprocess, "run", run)

    build_portable._smoke_test_frozen_cli(tmp_path, require_toolchain=True)

    assert [call.args[0][1:] for call in run.call_args_list] == [
        ["--version"],
        ["doctor", "--json"],
    ]


def test_jbr_pruning_only_targets_known_jcef_resources(tmp_path) -> None:
    jbr = tmp_path / "jbr"
    bin_dir = jbr / "bin"
    lib_dir = jbr / "lib"
    bin_dir.mkdir(parents=True)
    lib_dir.mkdir()
    ignore = build_portable._jbr_ignore(jbr)

    ignored_bin = ignore(
        str(bin_dir),
        ["java.exe", "keytool.exe", "libcef.dll", "awt.dll"],
    )
    ignored_lib = ignore(
        str(lib_dir),
        ["modules", "security", "locales", "resources.pak"],
    )

    assert ignored_bin == {"libcef.dll"}
    assert ignored_lib == {"locales", "resources.pak"}
    assert ignore(str(jbr / "conf"), ["security"]) == set()


def _playwright_browser_fixture(tmp_path):
    portable = tmp_path / "HapSign"
    browsers = (
        portable / "_internal" / "playwright" / "driver" / "package" / ".local-browsers"
    )
    chromium = browsers / "chromium-1"
    ffmpeg = browsers / "ffmpeg-1"
    chromium.mkdir(parents=True)
    ffmpeg.mkdir()
    (chromium / "chrome.exe").write_bytes(b"chrome")
    (ffmpeg / "ffmpeg.exe").write_bytes(b"ffmpeg")
    return portable, chromium, ffmpeg


def test_compat_build_keeps_chromium_but_removes_ffmpeg(tmp_path) -> None:
    portable, chromium, ffmpeg = _playwright_browser_fixture(tmp_path)

    build_portable._prune_playwright_extras(
        portable,
        keep_bundled_browser=True,
    )

    assert chromium.is_dir()
    assert not ffmpeg.exists()


def test_slim_build_removes_all_bundled_browser_resources(tmp_path) -> None:
    portable, chromium, ffmpeg = _playwright_browser_fixture(tmp_path)

    build_portable._prune_playwright_extras(
        portable,
        keep_bundled_browser=False,
    )

    assert not chromium.exists()
    assert not ffmpeg.exists()


def test_smoke_artifact_cleanup_preserves_application_files(tmp_path) -> None:
    portable = tmp_path / "HapSign"
    portable.mkdir()
    executable = portable / "HapSign.exe"
    executable.write_bytes(b"app")
    (portable / "hapsign-config.json").write_text("{}", encoding="utf-8")
    for name in ("logs", "signing_files", "signed_haps"):
        directory = portable / name
        directory.mkdir()
        (directory / "private-data").write_text("secret", encoding="utf-8")

    build_portable._remove_smoke_artifacts(portable)

    assert executable.is_file()
    assert not (portable / "hapsign-config.json").exists()
    assert not (portable / "logs").exists()
    assert not (portable / "signing_files").exists()
    assert not (portable / "signed_haps").exists()


def test_python_dependency_licenses_are_copied(tmp_path, monkeypatch) -> None:
    portable = tmp_path / "HapSign"
    portable.mkdir()
    python_root = tmp_path / "python"
    python_root.mkdir()
    (python_root / "LICENSE.txt").write_text("Python license\n", encoding="utf-8")
    # 不依赖各平台 Python 发行版是否把 LICENSE.txt 放在 base_prefix 根目录。
    monkeypatch.setattr(build_portable.sys, "base_prefix", str(python_root))
    monkeypatch.setattr(
        build_portable,
        "PYTHON_RUNTIME_DISTRIBUTIONS",
        ("requests",),
    )

    build_portable._copy_python_license_files(portable)

    license_root = portable / "licenses" / "python"
    assert (license_root / "PYTHON-LICENSE.txt").is_file()
    assert (license_root / "DISTRIBUTIONS.txt").is_file()
    assert (
        "requests"
        in (license_root / "DISTRIBUTIONS.txt").read_text(encoding="utf-8").lower()
    )
    assert any(
        path.name.lower().startswith(("license", "notice"))
        for path in license_root.rglob("*")
        if path.is_file()
    )


def test_runtime_license_list_covers_playwright_core_dependencies() -> None:
    listed = {
        build_portable._normalize_distribution_name(name)
        for name in build_portable.PYTHON_RUNTIME_DISTRIBUTIONS
    }
    for name in ("greenlet", "pyee"):
        assert build_portable._normalize_distribution_name(name) in listed


def _fake_requirement_map() -> dict[str, set[str]]:
    return {
        "playwright": {"greenlet", "pyee"},
        "greenlet": set(),
        "pyee": set(),
        "requests": {"urllib3", "certifi"},
        "urllib3": set(),
        "certifi": set(),
    }


def test_runtime_requirement_names_parses_versions_and_markers(monkeypatch) -> None:
    # 不模拟 _runtime_requirement_names，走真实解析：版本比较符/括号/条件标记必须剥离。
    raw = {
        "playwright": [
            "greenlet>=3.1.1 ; python_version >= '3.9'",
            "pyee (>=12.0.0) ; python_version >= '3.9'",
            "requests ; extra == 'test'",
        ],
    }
    monkeypatch.setattr(
        build_portable,
        "metadata",
        type(
            "FakeMetadata",
            (),
            {"requires": lambda name: raw.get(name)},
        ),
    )

    assert build_portable._runtime_requirement_names("playwright") == {
        "greenlet",
        "pyee",
    }


def test_frozen_runtime_closure_is_transitive_and_skips_extras(monkeypatch) -> None:
    deps = _fake_requirement_map()
    # 模拟带 extra 条件依赖的原始字符串，验证解析层会排除它们。
    raw = {
        "playwright": [
            "greenlet>=3.1.1 ; python_version >= '3.9'",
            "pyee (>=12.0.0) ; python_version >= '3.9'",
            "requests ; extra == 'test'",
        ],
    }
    monkeypatch.setattr(
        build_portable,
        "metadata",
        type(
            "FakeMetadata",
            (),
            {"requires": lambda name: raw.get(name)},
        ),
    )
    monkeypatch.setattr(
        build_portable,
        "_runtime_requirement_names",
        lambda name: deps.get(name, set()),
    )

    closure = build_portable._frozen_runtime_closure(("playwright", "requests"))

    assert closure == {
        build_portable._normalize_distribution_name(name)
        for name in ("playwright", "greenlet", "pyee", "requests", "urllib3", "certifi")
    }


def test_normalize_distribution_name_follows_pep503() -> None:
    assert (
        build_portable._normalize_distribution_name("PySide6-Essentials")
        == "pyside6-essentials"
    )
    assert build_portable._normalize_distribution_name("charset_normalizer") == (
        "charset-normalizer"
    )


def test_license_gate_passes_when_closure_is_covered(monkeypatch) -> None:
    deps = _fake_requirement_map()
    monkeypatch.setattr(
        build_portable,
        "_runtime_requirement_names",
        lambda name: deps.get(name, set()),
    )
    monkeypatch.setattr(
        build_portable,
        "PYTHON_RUNTIME_DISTRIBUTIONS",
        ("playwright", "greenlet", "pyee", "requests", "urllib3", "certifi"),
    )
    monkeypatch.setattr(
        build_portable, "BUILD_TIME_ONLY_DISTRIBUTIONS", ("PyInstaller",)
    )

    build_portable._validate_runtime_license_coverage()


def test_license_gate_fails_when_closure_misses_manifest(monkeypatch) -> None:
    deps = _fake_requirement_map()
    monkeypatch.setattr(
        build_portable,
        "_runtime_requirement_names",
        lambda name: deps.get(name, set()),
    )
    # playwright 的传递依赖 greenlet/pyee 不在清单中 → 门禁必须失败。
    monkeypatch.setattr(
        build_portable,
        "PYTHON_RUNTIME_DISTRIBUTIONS",
        ("playwright",),
    )
    monkeypatch.setattr(build_portable, "BUILD_TIME_ONLY_DISTRIBUTIONS", ())

    with pytest.raises(RuntimeError, match="greenlet"):
        build_portable._validate_runtime_license_coverage()


def test_license_gate_ignores_build_time_only_distributions(monkeypatch) -> None:
    deps = _fake_requirement_map()
    # PyInstaller 及其传递依赖不会被冻结，不要求进入清单。
    monkeypatch.setattr(
        build_portable,
        "_runtime_requirement_names",
        lambda name: deps.get(name, set()),
    )
    monkeypatch.setattr(
        build_portable,
        "PYTHON_RUNTIME_DISTRIBUTIONS",
        ("playwright", "greenlet", "pyee", "PyInstaller"),
    )
    monkeypatch.setattr(
        build_portable,
        "BUILD_TIME_ONLY_DISTRIBUTIONS",
        ("PyInstaller",),
    )

    build_portable._validate_runtime_license_coverage()


def test_release_documents_include_open_source_notices(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    portable = tmp_path / "HapSign"
    (project / "docs").mkdir(parents=True)
    portable.mkdir()
    sources = {
        "PORTABLE.md": "portable",
        "LICENSE": "MIT",
        "PRIVACY.md": "privacy",
        "THIRD_PARTY_NOTICES.md": "notices",
        "docs/PACKAGING.md": "building",
        "docs/OPEN_SOURCE_RELEASE.md": "release",
        "docs/AGENT_SIGNING.md": "agent",
        "docs/MIGRATIONS.md": "migrations",
    }
    for name, content in sources.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(build_portable, "PROJECT_ROOT", project)
    monkeypatch.setattr(
        build_portable,
        "_copy_python_license_files",
        lambda root: None,
    )

    build_portable._copy_release_documents(portable)

    for name in (
        "README.md",
        "LICENSE",
        "PRIVACY.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILDING.md",
        "OPEN_SOURCE_RELEASE.md",
        "AGENT_SIGNING.md",
        "MIGRATIONS.md",
    ):
        assert (portable / name).is_file()


def test_copy_toolchain_prefers_verified_prepared_directory(
    tmp_path,
    monkeypatch,
) -> None:
    project = tmp_path / "project"
    prepared_root = project / "build" / "toolchain-prepared"
    prepared = prepared_root / "windows"
    portable = tmp_path / "HapSign"
    for path in (
        prepared / "runtime" / "bin" / "java.exe",
        prepared / "runtime" / "bin" / "keytool.exe",
        prepared / "lib" / "hap-sign-tool.jar",
        prepared / "bin" / "hdc.exe",
        prepared / "PROVENANCE.txt",
        prepared / "toolchain.lock.json",
        prepared / "NOTICE.txt",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"verified")

    monkeypatch.setattr(build_portable, "PROJECT_ROOT", project)
    monkeypatch.setattr(build_portable, "PREPARED_TOOLCHAIN_ROOT", prepared_root)
    monkeypatch.setattr("hapsign.runtime.platform_tag", lambda: "windows")
    monkeypatch.setattr(build_portable, "_smoke_test_toolchain", lambda root: None)

    build_portable._copy_toolchain(
        portable,
        keep_full_jbr=False,
        allow_local_toolchain=False,
    )

    target = portable / "resources" / "toolchain" / "windows"
    assert (target / "runtime" / "bin" / "java.exe").read_bytes() == b"verified"
    assert (target / "PROVENANCE.txt").is_file()


def test_copy_toolchain_requires_explicit_deveco_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        build_portable,
        "PREPARED_TOOLCHAIN_ROOT",
        tmp_path / "missing",
    )
    monkeypatch.setattr("hapsign.runtime.platform_tag", lambda: "windows")

    with pytest.raises(RuntimeError, match="prepare_toolchain.py"):
        build_portable._copy_toolchain(
            tmp_path / "HapSign",
            keep_full_jbr=False,
            allow_local_toolchain=False,
        )


def test_local_linux_toolchain_copies_libusb_shared_object(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "deveco"
    runtime = source / "jbr"
    toolchains = source / "toolchains"
    java = runtime / "bin" / "java"
    keytool = runtime / "bin" / "keytool"
    signer = toolchains / "lib" / "hap-sign-tool.jar"
    hdc = toolchains / "hdc"
    libusb = toolchains / "libusb_shared.so"
    for path in (java, keytool, signer, hdc, libusb):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
        path.chmod(path.stat().st_mode | 0o111)
    selected = ToolchainPaths(
        java=java,
        keytool=keytool,
        hap_sign_tool=signer,
        hdc=hdc,
        source="test DevEco",
    )
    portable = tmp_path / "HapSign"
    monkeypatch.setattr("hapsign.runtime.discover_toolchain", lambda: selected)
    monkeypatch.setattr("hapsign.runtime.platform_tag", lambda: "linux")
    monkeypatch.setattr(build_portable, "_smoke_test_toolchain", lambda _root: None)

    build_portable._copy_local_toolchain(portable, keep_full_jbr=False)

    target = portable / "resources" / "toolchain" / "linux"
    assert (target / "bin" / "libusb_shared.so").read_bytes() == (libusb.read_bytes())
