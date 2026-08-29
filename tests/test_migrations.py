"""兼容性变更目录和旧缓存迁移测试。"""

import json
from datetime import date

import pytest

from hapsign import migrations
from hapsign.migrations import (
    LEGACY_CACHE_CHANGE_ID,
    LEGACY_STATE_CHANGE_ID,
    breaking_changes,
    cache_compatibility_warning,
    legacy_state_warning,
    migrate_legacy_cache,
)


def _write_metadata(path, **overrides) -> None:
    metadata = {
        "creation_date": date.today().isoformat(),
        "bundle_name": "com.example.app",
        "udid": "A" * 64,
        "p12_path": "key.p12",
        "cer_path": "cert.cer",
        "p7b_path": "profile.p7b",
        **overrides,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata), encoding="utf-8")
    for name in ("key.p12", "cert.cer", "profile.p7b"):
        (path.parent / name).write_bytes(b"material")


def test_breaking_change_catalog_has_stable_unique_ids() -> None:
    changes = breaking_changes()

    ids = [change["id"] for change in changes]
    assert len(ids) == len(set(ids))
    assert LEGACY_CACHE_CHANGE_ID in ids
    state_change = next(
        change for change in changes if change["id"] == LEGACY_STATE_CHANGE_ID
    )
    assert state_change["destructive"] is True
    assert all(
        change["decision"] == "accepted"
        and change["compatibility_strategy"]
        in {
            "configuration",
            "configuration-and-explicit-migration",
            "migration-only",
        }
        and change["compatibility_options"]
        and "remediation" in change
        and "docs" in change
        for change in changes
    )


def test_cache_warning_detects_missing_capability_mode(tmp_path) -> None:
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata)

    warning = cache_compatibility_warning(metadata)

    assert warning is not None
    assert warning["id"] == LEGACY_CACHE_CHANGE_ID
    assert warning["destructive"] is True
    assert warning["requires_user_decision"] is True
    assert warning["migratable"] is True
    assert warning["reasons"] == ["missing_capability_mode"]

    _write_metadata(metadata, enable_capability=False)
    assert cache_compatibility_warning(metadata) is None


def test_cache_warning_detects_capability_mode_mismatch(tmp_path) -> None:
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata, enable_capability=True)

    warning = cache_compatibility_warning(metadata, enable_capability=False)

    assert warning is not None
    assert warning["migratable"] is False
    assert warning["reasons"] == ["capability_mode_mismatch"]
    assert warning["cached_capability_mode"] == "system-basic"
    assert warning["expected_capability_mode"] == "normal"
    assert cache_compatibility_warning(metadata, enable_capability=True) is None

    _write_metadata(
        metadata,
        enable_capability=False,
        requested_enable_capability=True,
    )
    assert cache_compatibility_warning(metadata, enable_capability=True) is None

    _write_metadata(
        metadata,
        enable_capability=True,
        requested_enable_capability=False,
    )
    warning = cache_compatibility_warning(metadata, enable_capability=False)
    assert warning is not None
    assert warning["reasons"] == ["invalid_effective_capability_mode"]


def test_legacy_state_warning_reports_home_cache_not_selected(tmp_path) -> None:
    state = tmp_path / "application-state"
    work = state / "com.example.app"
    legacy = tmp_path / ".hapsign"
    legacy.mkdir()
    (legacy / ".token_cache.json").write_bytes(b"token")
    _write_metadata(legacy / "com.example.app" / "metadata.json")

    warning = legacy_state_warning(
        state,
        work,
        bundle_name="com.example.app",
        legacy_state_dir=legacy,
    )

    assert warning is not None
    assert warning["id"] == LEGACY_STATE_CHANGE_ID
    assert warning["destructive"] is True
    assert warning["requires_user_decision"] is True
    assert warning["found"] == ["token_cache", "signing_materials"]
    assert warning["legacy_state_dir"] == str(legacy.resolve())

    assert (
        legacy_state_warning(
            legacy,
            legacy / "com.example.app",
            bundle_name="com.example.app",
            legacy_state_dir=legacy,
        )
        is None
    )


def test_legacy_state_warning_does_not_block_for_token_only(tmp_path) -> None:
    state = tmp_path / "application-state"
    legacy = tmp_path / ".hapsign"
    legacy.mkdir()
    (legacy / ".token_cache.json").write_bytes(b"token")

    warning = legacy_state_warning(
        state,
        state / "com.example.app",
        bundle_name="com.example.app",
        legacy_state_dir=legacy,
    )

    assert warning is not None
    assert warning["destructive"] is False
    assert warning["requires_user_decision"] is False
    assert warning["found"] == ["token_cache"]


def test_frozen_build_detects_legacy_program_directory(tmp_path, monkeypatch) -> None:
    application = tmp_path / "old-portable"
    legacy = application / "signing_files"
    state = tmp_path / "shared-state"
    legacy.mkdir(parents=True)
    (legacy / ".token_cache.json").write_bytes(b"token")
    monkeypatch.setattr(migrations.sys, "frozen", True, raising=False)
    monkeypatch.setattr(migrations, "application_dir", lambda: application)
    monkeypatch.setattr(migrations.Path, "home", lambda: tmp_path / "home")

    warning = legacy_state_warning(
        state,
        state / "com.example.app",
        bundle_name="com.example.app",
    )

    assert warning is not None
    assert warning["legacy_state_dir"] == str(legacy.resolve())
    assert warning["found"] == ["token_cache"]


def test_migrate_legacy_cache_is_atomic_backed_up_and_idempotent(tmp_path) -> None:
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata)
    original = metadata.read_bytes()

    result = migrate_legacy_cache(
        metadata,
        bundle_name="com.example.app",
        enable_capability=False,
    )

    backup = tmp_path / "metadata.json.pre-capability-migration.bak"
    assert result["changed"] is True
    assert result["backup"] == str(backup)
    assert backup.read_bytes() == original
    assert (
        json.loads(metadata.read_text(encoding="utf-8"))["enable_capability"] is False
    )
    migrated = json.loads(metadata.read_text(encoding="utf-8"))
    assert migrated["requested_enable_capability"] is False
    assert migrated["p12_path"] == str((tmp_path / "key.p12").resolve())
    assert not (tmp_path / "metadata.json.tmp").exists()

    repeated = migrate_legacy_cache(
        metadata,
        bundle_name="com.example.app",
        enable_capability=False,
    )
    assert repeated["changed"] is False


def test_migrate_legacy_cache_rejects_bundle_or_mode_mismatch(tmp_path) -> None:
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata)

    with pytest.raises(ValueError, match="bundle_name"):
        migrate_legacy_cache(
            metadata,
            bundle_name="com.other.app",
            enable_capability=False,
        )

    _write_metadata(metadata, enable_capability=True)
    with pytest.raises(ValueError, match="其他能力模式"):
        migrate_legacy_cache(
            metadata,
            bundle_name="com.example.app",
            enable_capability=False,
        )


def test_invalid_legacy_udid_is_reported_but_not_migratable(tmp_path) -> None:
    metadata = tmp_path / "metadata.json"
    _write_metadata(metadata, udid="invalid")

    warning = cache_compatibility_warning(
        metadata,
        bundle_name="com.example.app",
    )

    assert warning is not None
    assert warning["migratable"] is False
    assert warning["reasons"] == [
        "missing_capability_mode",
        "invalid_device_udid",
    ]
    with pytest.raises(ValueError, match="设备 UDID"):
        migrate_legacy_cache(
            metadata,
            bundle_name="com.example.app",
            enable_capability=False,
        )
