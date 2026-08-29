"""Machine-readable build metadata shared by source and frozen distributions."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from hapsign import __version__
from hapsign.runtime import application_dir, platform_tag, resource_dir

BUILD_INFO_SCHEMA = 1
CLI_PROTOCOL_VERSION = 2
BUILD_INFO_FILENAME = "BUILD_INFO.json"
EDITIONS = {"source", "external_toolchain", "portable", "gui"}


def architecture_tag() -> str:
    """Return the release architecture name used in asset manifests."""
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "unknown"


def _inferred_edition() -> str:
    if not getattr(sys, "frozen", False):
        return "source"
    toolchain_root = resource_dir() / "toolchain" / platform_tag()
    return "portable" if toolchain_root.is_dir() else "external_toolchain"


def _defaults() -> dict[str, object]:
    edition = _inferred_edition()
    return {
        "schema": BUILD_INFO_SCHEMA,
        "version": __version__,
        "release_version": __version__,
        "cli_protocol": CLI_PROTOCOL_VERSION,
        "product": "HapSign source",
        "edition": edition,
        "platform": platform_tag(),
        "architecture": architecture_tag(),
        "bundled_python": bool(getattr(sys, "frozen", False)),
        "bundled_toolchain": edition in {"portable", "gui"},
        "bundled_browser": edition == "gui",
        "code_signed": False,
        "publisher_signed": False,
        "macos_adhoc_signature": False,
        "notarized": False,
    }


def build_info_path() -> Path:
    return application_dir() / BUILD_INFO_FILENAME


def build_info() -> dict[str, object]:
    """Read trusted package metadata, falling back to safe runtime inference."""
    info = _defaults()
    path = build_info_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        loaded = None
    if isinstance(loaded, dict):
        for key in info:
            if key in loaded:
                info[key] = loaded[key]

    edition = str(info.get("edition", ""))
    if edition not in EDITIONS:
        info["edition"] = _inferred_edition()
    info["schema"] = BUILD_INFO_SCHEMA
    info["version"] = __version__
    info["cli_protocol"] = CLI_PROTOCOL_VERSION
    return info
