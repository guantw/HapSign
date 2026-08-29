"""Build the supported HapSign release products for the current host.

Official products use no trusted publisher identity.  PyInstaller may apply the
mandatory identity-less ad-hoc signature on macOS arm64.  This script never
copies a local DevEco installation into a public artifact: Portable and GUI
products can only use the locked public toolchain prepared by
``prepare_toolchain.py``.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
build_portable = importlib.import_module("scripts.build_portable")

DIST_DIR = PROJECT_ROOT / "dist"
PRODUCTS_DIR = DIST_DIR / "products"
ASSETS_DIR = DIST_DIR / "release-assets"

GUI = "gui"
EXTERNAL = "external_toolchain"
PORTABLE = "portable"

PRODUCT_NAMES = {
    GUI: "HapSign GUI",
    EXTERNAL: "HapSign CLI External Toolchain",
    PORTABLE: "HapSign CLI Portable",
}
PRODUCT_DIRECTORIES = {
    GUI: "HapSign-GUI",
    EXTERNAL: "HapSign-CLI-ExternalToolchain",
    PORTABLE: "HapSign-CLI-Portable",
}
PRODUCT_SLUGS = {
    GUI: "HapSign-GUI",
    EXTERNAL: "HapSign-CLI-ExternalToolchain",
    PORTABLE: "HapSign-CLI-Portable",
}
README_SOURCES = {
    GUI: "docs/GUI_DISTRIBUTION.md",
    EXTERNAL: "docs/CLI_EXTERNAL_TOOLCHAIN.md",
    PORTABLE: "docs/CLI_PORTABLE.md",
}
SUPPORTED_PRODUCTS = {
    ("windows", "x64"): (GUI, EXTERNAL, PORTABLE),
    ("linux", "x64"): (EXTERNAL, PORTABLE),
    ("macos", "arm64"): (EXTERNAL,),
}


def release_version(value: str) -> str:
    """Convert a PEP 440 prerelease into the release asset spelling."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(?:(a|b|rc)(\d+))?", value)
    if not match:
        raise RuntimeError(f"Unsupported release version: {value}")
    base, phase, number = match.groups()
    if phase is None:
        return base
    aliases = {"a": "alpha", "b": "beta", "rc": "rc"}
    return f"{base}-{aliases[phase]}.{number}"


def host_platform() -> str:
    system = platform.system().lower()
    return "macos" if system == "darwin" else system


def host_architecture() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "unknown"


def supported_products(platform_name: str, architecture: str) -> tuple[str, ...]:
    products = SUPPORTED_PRODUCTS.get((platform_name, architecture))
    if products is None:
        if platform_name == "macos" and architecture == "x64":
            raise RuntimeError("macOS x64 is explicitly unsupported; use macOS arm64")
        raise RuntimeError(f"Unsupported release host: {platform_name}-{architecture}")
    return products


def asset_filename(
    product: str,
    version: str,
    platform_name: str,
    architecture: str,
) -> str:
    extension, _ = build_portable._portable_archive_settings(platform_name)
    return (
        f"{PRODUCT_SLUGS[product]}-v{version}-{platform_name}-{architecture}{extension}"
    )


def _require_modules(*names: str) -> None:
    missing = [name for name in names if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError(
            "Missing build dependencies: "
            f"{', '.join(missing)}. Install the matching project extras first."
        )


def _run(command: list[str], *, env: dict[str, str]) -> None:
    print("+", subprocess.list2cmdline(command))
    subprocess.run(command, cwd=PROJECT_ROOT, env=env, check=True)


def _cli_executable_name() -> str:
    return "hapsign-cli.exe" if sys.platform == "win32" else "hapsign-cli"


def _write_build_info(
    root: Path,
    *,
    product: str,
    version: str,
    platform_name: str,
    architecture: str,
) -> Path:
    from hapsign import __version__

    payload = {
        "schema": 1,
        "version": __version__,
        "release_version": version,
        "cli_protocol": 2,
        "product": PRODUCT_NAMES[product],
        "edition": product,
        "platform": platform_name,
        "architecture": architecture,
        "bundled_python": True,
        "bundled_toolchain": product in {GUI, PORTABLE},
        "bundled_browser": product == GUI,
        "code_signed": platform_name == "macos",
        "publisher_signed": False,
        "macos_adhoc_signature": platform_name == "macos",
        "notarized": False,
        "code_signing_policy": (
            "No trusted publisher identity is used. macOS arm64 receives only "
            "the mandatory PyInstaller ad-hoc signature."
        ),
    }
    path = root / "BUILD_INFO.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _copy_documents(root: Path, *, product: str) -> None:
    files = {
        README_SOURCES[product]: "README.md",
        "LICENSE": "LICENSE",
        "PRIVACY.md": "PRIVACY.md",
        "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md",
        "docs/AGENT_SIGNING.md": "AGENT_SIGNING.md",
        "docs/MIGRATIONS.md": "MIGRATIONS.md",
        "docs/PACKAGING.md": "BUILDING.md",
        "docs/OPEN_SOURCE_RELEASE.md": "OPEN_SOURCE_RELEASE.md",
        "docs/TESTED_ENVIRONMENTS.md": "TESTED_ENVIRONMENTS.md",
    }
    for source_name, target_name in files.items():
        shutil.copy2(PROJECT_ROOT / source_name, root / target_name)
    distributions = (
        build_portable.PYTHON_RUNTIME_DISTRIBUTIONS
        if product == GUI
        else build_portable.CLI_RUNTIME_DISTRIBUTIONS
    )
    build_portable._copy_python_license_files(root, distributions)


def _build_cli_base(*, env: dict[str, str]) -> Path:
    cli_dist = PROJECT_ROOT / "build" / "release-cli-dist"
    cli_spec = PROJECT_ROOT / "build" / "release-cli-spec"
    cli_spec.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onedir",
            "--console",
            "--name",
            "hapsign-cli",
            "--additional-hooks-dir",
            str(PROJECT_ROOT / "bundle" / "hooks"),
            "--distpath",
            str(cli_dist),
            "--workpath",
            str(PROJECT_ROOT / "build" / "release-cli-work"),
            "--specpath",
            str(cli_spec),
            str(PROJECT_ROOT / "main.py"),
        ],
        env=env,
    )
    root = cli_dist / "hapsign-cli"
    executable = root / _cli_executable_name()
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create the CLI: {executable}")
    return root


def _build_gui_base(*, env: dict[str, str]) -> Path:
    gui_dist = PROJECT_ROOT / "build" / "release-gui-dist"
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(gui_dist),
            "--workpath",
            str(PROJECT_ROOT / "build" / "release-gui-work"),
            str(PROJECT_ROOT / "bundle" / "hapsign.spec"),
        ],
        env=env,
    )
    root = gui_dist / "HapSign"
    executable = root / "HapSign.exe"
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller did not create the GUI: {executable}")
    return root


def _fresh_copy(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)


def _smoke_test_cli(root: Path, *, require_toolchain: bool) -> None:
    executable = root / _cli_executable_name()
    checks = [([str(executable), "--version"], {0})]
    checks.append(([str(executable), "build-info", "--json"], {0}))
    if require_toolchain:
        checks.append(([str(executable), "doctor", "--json"], {0}))
    for command, expected_codes in checks:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode not in expected_codes:
            details = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"Frozen CLI smoke test failed ({result.returncode}): {details}"
            )
        if "--json" in command:
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Frozen CLI did not return valid JSON") from exc
            if payload.get("ok") is not True:
                raise RuntimeError(f"Frozen CLI reported failure: {payload}")


def _archive_product(
    root: Path,
    *,
    product: str,
    version: str,
    platform_name: str,
    architecture: str,
) -> Path:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    filename = asset_filename(product, version, platform_name, architecture)
    extension, archive_format = build_portable._portable_archive_settings(platform_name)
    archive_path = ASSETS_DIR / filename
    base_name = str(archive_path)[: -len(extension)]
    if archive_path.exists():
        archive_path.unlink()
    generated = Path(
        shutil.make_archive(
            base_name,
            archive_format,
            root_dir=root.parent,
            base_dir=root.name,
        )
    )
    if generated != archive_path:
        raise RuntimeError(f"Unexpected archive path: {generated} != {archive_path}")
    build_portable._write_sha256_file(archive_path)
    return archive_path


def build_release(
    requested_products: tuple[str, ...] | None = None,
) -> list[Path]:
    from hapsign import __version__

    platform_name = host_platform()
    architecture = host_architecture()
    allowed = supported_products(platform_name, architecture)
    products = requested_products or allowed
    invalid = sorted(set(products) - set(allowed))
    if invalid:
        raise RuntimeError(
            f"Products not supported on {platform_name}-{architecture}: "
            f"{', '.join(invalid)}"
        )

    version = release_version(__version__)
    PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    archives: list[Path] = []

    cli_products = tuple(
        product for product in products if product in {EXTERNAL, PORTABLE}
    )
    if cli_products:
        _require_modules("PyInstaller", "playwright")
        build_portable._validate_runtime_license_coverage(
            build_portable.CLI_RUNTIME_DISTRIBUTIONS
        )
        env["HAPSIGN_BUNDLE_CHROMIUM"] = "0"
        cli_base = _build_cli_base(env=env)
        for product in cli_products:
            root = PRODUCTS_DIR / PRODUCT_DIRECTORIES[product]
            _fresh_copy(cli_base, root)
            build_portable._prune_playwright_extras(root, keep_bundled_browser=False)
            if product == PORTABLE:
                build_portable._copy_toolchain(
                    root,
                    keep_full_jbr=False,
                    allow_local_toolchain=False,
                )
            _write_build_info(
                root,
                product=product,
                version=version,
                platform_name=platform_name,
                architecture=architecture,
            )
            _copy_documents(root, product=product)
            _smoke_test_cli(root, require_toolchain=product == PORTABLE)
            archives.append(
                _archive_product(
                    root,
                    product=product,
                    version=version,
                    platform_name=platform_name,
                    architecture=architecture,
                )
            )

    if GUI in products:
        _require_modules("PyInstaller", "PySide6", "playwright")
        build_portable._validate_runtime_license_coverage()
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
        build_portable._require_playwright_browser()
        env = os.environ.copy()
        env["HAPSIGN_BUNDLE_CHROMIUM"] = "1"
        gui_base = _build_gui_base(env=env)
        root = PRODUCTS_DIR / PRODUCT_DIRECTORIES[GUI]
        _fresh_copy(gui_base, root)
        build_portable._prune_playwright_extras(root, keep_bundled_browser=True)
        build_portable._copy_toolchain(
            root,
            keep_full_jbr=False,
            allow_local_toolchain=False,
        )
        _write_build_info(
            root,
            product=GUI,
            version=version,
            platform_name=platform_name,
            architecture=architecture,
        )
        _copy_documents(root, product=GUI)
        build_portable._smoke_test_frozen_app(root, keep_bundled_browser=True)
        archives.append(
            _archive_product(
                root,
                product=GUI,
                version=version,
                platform_name=platform_name,
                architecture=architecture,
            )
        )

    return archives


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--product",
        action="append",
        choices=(GUI, EXTERNAL, PORTABLE),
        help=(
            "Build only this product; repeat as needed. Defaults to all host products."
        ),
    )
    args = parser.parse_args()
    requested = tuple(dict.fromkeys(args.product)) if args.product else None
    try:
        archives = build_release(requested)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1
    for archive in archives:
        print(f"Release asset created: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
