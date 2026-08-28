# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_all

project_root = Path(SPECPATH).parent
app_manifest = (
    str(project_root / "bundle" / "hapsign.manifest")
    if sys.platform == "win32"
    else None
)
playwright_datas, playwright_binaries, playwright_hiddenimports = collect_all(
    "playwright"
)
if os.environ.get("HAPSIGN_BUNDLE_CHROMIUM", "0") != "1":
    playwright_datas = [
        item for item in playwright_datas if ".local-browsers" not in item[0]
    ]
    playwright_binaries = [
        item for item in playwright_binaries if ".local-browsers" not in item[0]
    ]

analysis = Analysis(
    [str(project_root / "hapsign" / "gui.py")],
    pathex=[str(project_root)],
    binaries=playwright_binaries,
    datas=playwright_datas,
    hiddenimports=playwright_hiddenimports,
    hookspath=[str(project_root / "bundle" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "OpenSSL",
        "cryptography",
        "pytest",
        "pytest_cov",
        "ruff",
        "setuptools",
        "wheel",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="HapSign",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    manifest=app_manifest,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="HapSign",
)
