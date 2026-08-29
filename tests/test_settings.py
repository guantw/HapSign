"""桌面版设置与目录规则测试。"""

import json

from hapsign import settings


def test_load_missing_config_uses_safe_defaults(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAPSIGN_CONFIG_FILE", str(tmp_path / "missing.json"))

    loaded = settings.load_settings()

    assert loaded == settings.AppSettings()
    assert loaded.browser_mode == "system_controlled"
    assert loaded.signing_storage == "program"
    assert loaded.log_sensitive_data is False
    assert loaded.keep_signed_hap is True


def test_settings_round_trip(tmp_path, monkeypatch) -> None:
    config = tmp_path / "config" / "hapsign.json"
    monkeypatch.setenv("HAPSIGN_CONFIG_FILE", str(config))
    expected = settings.AppSettings(
        log_level="DEBUG",
        signing_storage="custom",
        custom_signing_dir=str(tmp_path / "keys"),
        browser_mode="system",
        log_sensitive_data=True,
        keep_signed_hap=False,
    )

    assert settings.save_settings(expected) == config
    assert settings.load_settings() == expected
    assert not config.with_suffix(".json.tmp").exists()


def test_invalid_values_fall_back_and_sensitive_requires_boolean(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "hapsign.json"
    monkeypatch.setenv("HAPSIGN_CONFIG_FILE", str(config))
    config.write_text(
        json.dumps(
            {
                "log_level": "verbose",
                "signing_storage": "unknown",
                "browser_mode": "other",
                "log_sensitive_data": "true",
                "keep_signed_hap": "false",
            }
        ),
        encoding="utf-8",
    )

    loaded = settings.load_settings()

    assert loaded.log_level == "INFO"
    assert loaded.signing_storage == "program"
    assert loaded.browser_mode == "system_controlled"
    assert loaded.log_sensitive_data is False
    assert loaded.keep_signed_hap is True


def test_signing_directory_modes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "application_dir", lambda: tmp_path / "app")
    monkeypatch.setattr(settings, "user_local_data_dir", lambda: tmp_path / "local")

    assert settings.signing_files_dir(settings.AppSettings()) == (
        tmp_path / "app" / "signing_files"
    )
    assert settings.signing_files_dir(
        settings.AppSettings(signing_storage="appdata")
    ) == (tmp_path / "local" / "signing_files")
    assert settings.signing_files_dir(
        settings.AppSettings(
            signing_storage="custom",
            custom_signing_dir=str(tmp_path / "custom"),
        )
    ) == (tmp_path / "custom")


def test_data_directory_environment_override_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAPSIGN_DATA_DIR", str(tmp_path / "override"))

    assert settings.signing_files_dir(
        settings.AppSettings(signing_storage="appdata")
    ) == (tmp_path / "override" / "signing_files")


def test_exact_signing_directory_environment_override_wins(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("HAPSIGN_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("HAPSIGN_SIGNING_DIR", str(tmp_path / "exact-state"))

    assert settings.signing_files_dir(settings.AppSettings()) == (
        tmp_path / "exact-state"
    )


def test_signed_hap_directory_is_always_in_program_directory(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "application_dir", lambda: tmp_path / "app")
    monkeypatch.setenv("HAPSIGN_DATA_DIR", str(tmp_path / "elsewhere"))

    assert settings.signed_haps_dir() == tmp_path / "app" / "signed_haps"


def test_signed_hap_directory_environment_override_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAPSIGN_SIGNED_HAPS_DIR", str(tmp_path / "outputs"))

    assert settings.signed_haps_dir() == tmp_path / "outputs"
