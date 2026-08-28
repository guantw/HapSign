"""签名流程中的本地、无网络逻辑测试。"""

import base64
import json
import logging
import os
import threading
import zipfile
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hapsign.api.client import TokenExpiredError
from hapsign.cancellation import OperationCancelled
from hapsign.config import ACL_PERMISSION_WHITELIST
from hapsign.diagnostics import set_sensitive_logging
from hapsign.models import TokenInfo
from hapsign.pipeline import SignPipeline
from hapsign.token import secure_token_cache


@pytest.fixture(autouse=True)
def _isolate_default_state_dir(tmp_path, monkeypatch) -> None:
    """禁止无 state-dir 的测试接触真实用户主目录。"""
    monkeypatch.setattr(
        "hapsign.pipeline.default_state_dir",
        lambda: str(tmp_path / "signing_files"),
    )


def _pipeline(tmp_path, monkeypatch) -> SignPipeline:
    monkeypatch.chdir(tmp_path)
    return SignPipeline(
        hap_path="app.hap",
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
    )


def _fake_dpapi(monkeypatch) -> None:
    """用可逆的确定性伪加密替代真实 DPAPI。

    使加密格式相关测试不依赖系统用户配置（沙箱/服务账户可能没有可用的
    用户配置，CryptProtectData 会失败），任何平台都能稳定运行。
    """
    monkeypatch.setattr(
        secure_token_cache, "_dpapi_protect", lambda data: base64.b64encode(data)
    )

    def _unprotect(data: bytes) -> bytes:
        try:
            return base64.b64decode(data)
        except ValueError as exc:
            raise OSError(f"DPAPI 解密失败（测试伪实现）: {exc}") from None

    monkeypatch.setattr(secure_token_cache, "_dpapi_unprotect", _unprotect)
    # 让 protect/decrypt 的跨平台守卫走 DPAPI 分支；测试结束后由 monkeypatch 恢复。
    monkeypatch.setattr(secure_token_cache.os, "name", "nt")


def test_token_cache_round_trip(tmp_path, monkeypatch) -> None:
    _fake_dpapi(monkeypatch)
    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline._token_info = TokenInfo(
        access_token="access",
        refresh_token="refresh",
        user_id="user",
        jwt_token="jwt",
    )

    pipeline._save_token_cache()

    assert pipeline._load_token_cache()["access_token"] == "access"
    assert not (tmp_path / "signing_files" / ".token_cache.json.tmp").exists()


def test_token_cache_write_failure_keeps_old_file(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text('{"old": true}', encoding="utf-8")
    pipeline._token_info = TokenInfo(
        access_token="access",
        user_id="user",
        jwt_token="jwt",
    )

    def _broken_dumps(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("hapsign.pipeline.json.dumps", _broken_dumps)

    pipeline._save_token_cache()

    assert cache_path.read_text(encoding="utf-8") == '{"old": true}'
    assert not (tmp_path / "signing_files" / ".token_cache.json.tmp").exists()


def test_token_cache_replace_failure_keeps_old_file(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text('{"old": true}', encoding="utf-8")
    pipeline._token_info = TokenInfo(
        access_token="access",
        user_id="user",
        jwt_token="jwt",
    )

    def _broken_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr("hapsign.pipeline.os.replace", _broken_replace)

    pipeline._save_token_cache()

    assert cache_path.read_text(encoding="utf-8") == '{"old": true}'
    assert not (tmp_path / "signing_files" / ".token_cache.json.tmp").exists()


def test_token_cache_stale_tmp_does_not_break_load(tmp_path, monkeypatch) -> None:
    _fake_dpapi(monkeypatch)
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    # 残留的半截 tmp 文件不应被当成正式缓存。
    cache_path.with_suffix(cache_path.suffix + ".tmp").write_text(
        '{"access_tok', encoding="utf-8"
    )

    assert pipeline._load_token_cache() is None

    pipeline._token_info = TokenInfo(
        access_token="access",
        user_id="user",
        jwt_token="jwt",
    )
    pipeline._save_token_cache()

    assert pipeline._load_token_cache()["access_token"] == "access"
    assert not cache_path.with_suffix(cache_path.suffix + ".tmp").exists()


def test_custom_state_dir_owns_token_cache(tmp_path, monkeypatch) -> None:
    _fake_dpapi(monkeypatch)
    monkeypatch.chdir(tmp_path)
    state_dir = tmp_path / "app-data"
    pipeline = SignPipeline(
        hap_path="app.hap",
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "work"),
        state_dir=str(state_dir),
    )
    pipeline._token_info = TokenInfo(
        access_token="access",
        user_id="user",
        jwt_token="jwt",
    )

    pipeline._save_token_cache()

    assert (state_dir / ".token_cache.json").is_file()
    assert not (tmp_path / "signing_files" / ".token_cache.json").exists()


def test_expired_token_cache_is_ignored(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text(
        json.dumps(
            {
                "creation_date": (date.today() - timedelta(days=1)).isoformat(),
                "access_token": "access",
                "user_id": "user",
                "jwt_token": "jwt",
            }
        ),
        encoding="utf-8",
    )

    assert pipeline._load_token_cache() is None


def test_token_cache_without_jwt_is_ignored(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text(
        json.dumps(
            {
                "creation_date": date.today().isoformat(),
                "access_token": "access",
                "user_id": "user",
            }
        ),
        encoding="utf-8",
    )

    assert pipeline._load_token_cache() is None


def test_token_cache_encrypted_on_disk(tmp_path, monkeypatch) -> None:
    _fake_dpapi(monkeypatch)
    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline._token_info = TokenInfo(
        access_token="access",
        refresh_token="refresh",
        user_id="user",
        jwt_token="jwt",
    )

    pipeline._save_token_cache()

    raw = (tmp_path / "signing_files" / ".token_cache.json").read_bytes()
    assert raw.startswith(secure_token_cache._HEADER.encode("ascii"))
    assert b"access" not in raw


def test_token_cache_migrates_legacy_plaintext(tmp_path, monkeypatch) -> None:
    _fake_dpapi(monkeypatch)
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text(
        json.dumps(
            {
                "creation_date": date.today().isoformat(),
                "access_token": "access",
                "refresh_token": "refresh",
                "user_id": "user",
                "jwt_token": "jwt",
            }
        ),
        encoding="utf-8",
    )

    cache = pipeline._load_token_cache()

    assert cache is not None
    assert cache["access_token"] == "access"
    # 迁移后磁盘上应为加密格式。
    raw = cache_path.read_bytes()
    assert raw.startswith(secure_token_cache._HEADER.encode("ascii"))
    assert b"access" not in raw
    assert pipeline._load_token_cache()["access_token"] == "access"


def test_token_cache_plaintext_is_current_format_on_non_windows(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(secure_token_cache.os, "name", "posix")
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text(
        json.dumps(
            {
                "creation_date": date.today().isoformat(),
                "access_token": "access",
                "refresh_token": "refresh",
                "user_id": "user",
                "jwt_token": "jwt",
            }
        ),
        encoding="utf-8",
    )
    rewrite = Mock()
    monkeypatch.setattr(pipeline, "_write_token_cache", rewrite)

    assert pipeline._load_token_cache()["access_token"] == "access"
    rewrite.assert_not_called()


def test_authenticate_reuses_token_without_browser_or_device(
    tmp_path, monkeypatch
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache = {
        "creation_date": date.today().isoformat(),
        "access_token": "access",
        "refresh_token": "refresh",
        "user_id": "user",
        "jwt_token": "jwt",
    }
    monkeypatch.setattr(pipeline, "_load_token_cache", Mock(return_value=cache))
    init_client = Mock()
    monkeypatch.setattr(pipeline, "_init_client_from_cache", init_client)
    monkeypatch.setattr(
        pipeline,
        "_step_login",
        Mock(side_effect=AssertionError("browser should not open")),
    )
    monkeypatch.setattr(
        pipeline,
        "_step_check_device",
        Mock(side_effect=AssertionError("device should not be queried")),
    )

    result = pipeline.authenticate()

    assert result == {
        "authenticated": True,
        "from_cache": True,
        "creation_date": date.today().isoformat(),
    }
    init_client.assert_called_once_with(cache)


def test_token_cache_bad_base64_falls_back_to_relogin(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_bytes(b"hapsign-token-v1:!!!not-base64!!!")

    assert pipeline._load_token_cache() is None


def test_token_cache_corrupt_payload_falls_back_to_relogin(
    tmp_path, monkeypatch
) -> None:
    _fake_dpapi(monkeypatch)
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    # 头 + 合法 base64 但非 DPAPI blob：解密必须失败而不是崩溃。
    cache_path.write_bytes(b"hapsign-token-v1:" + base64.b64encode(b"not a dpapi blob"))

    assert pipeline._load_token_cache() is None


@pytest.mark.parametrize("payload", ["[]", "null", '"not-an-object"'])
def test_token_cache_non_object_falls_back_to_relogin(
    tmp_path, monkeypatch, payload
) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_text(payload, encoding="utf-8")

    assert pipeline._load_token_cache() is None


@pytest.mark.parametrize("payload", ["[]", "null", '"not-an-object"'])
def test_metadata_non_object_is_ignored(tmp_path, monkeypatch, payload) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline._metadata_path = str(tmp_path / "signing" / "metadata.json")
    with open(pipeline._metadata_path, "w", encoding="utf-8") as metadata:
        metadata.write(payload)

    assert pipeline._load_cached_metadata() is None


def test_pipeline_failure_logs_redacted_exception(
    caplog, tmp_path, monkeypatch
) -> None:
    set_sensitive_logging(False)
    pipeline = _pipeline(tmp_path, monkeypatch)
    secret = "secret-temp-token"
    device_udid = "A" * 64
    caplog.set_level(logging.DEBUG, logger="hapsign.pipeline")

    result = pipeline._run_steps(
        [
            (
                "登录",
                Mock(
                    side_effect=RuntimeError(
                        "request failed: "
                        f"/authrouter/auth/api/temptoken/check?tempToken={secret}; "
                        f"Device not found in list: {device_udid}"
                    )
                ),
            )
        ]
    )

    assert result is False
    assert secret not in caplog.text
    assert "tempToken=<redacted>" in caplog.text
    assert secret not in pipeline.last_error
    assert "tempToken=<redacted>" in pipeline.last_error
    assert device_udid not in caplog.text
    assert device_udid not in pipeline.last_error
    assert "Device not found in list: <redacted-udid>" in pipeline.last_error


def test_token_cache_protect_failure_keeps_old_file(tmp_path, monkeypatch) -> None:
    _fake_dpapi(monkeypatch)
    monkeypatch.setattr(
        secure_token_cache,
        "_dpapi_protect",
        Mock(side_effect=OSError("CryptProtectData 失败: 无法加载用户配置")),
    )
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_bytes(b"hapsign-token-v1:" + base64.b64encode(b"old-cache"))
    pipeline._token_info = TokenInfo(
        access_token="new",
        user_id="user",
        jwt_token="jwt",
    )

    pipeline._save_token_cache()

    # 加密失败：保留原缓存，绝不落明文。
    raw = cache_path.read_bytes()
    assert raw.startswith(secure_token_cache._HEADER.encode("ascii"))
    assert b"new" not in raw
    assert not (tmp_path / "signing_files" / ".token_cache.json.tmp").exists()


def test_token_cache_encrypted_cache_on_non_windows_ignored(
    tmp_path, monkeypatch
) -> None:
    _fake_dpapi(monkeypatch)
    # 模拟加密缓存被带到非 Windows 平台：decrypt 必须显式失败而不是崩溃。
    monkeypatch.setattr(secure_token_cache.os, "name", "posix")
    pipeline = _pipeline(tmp_path, monkeypatch)
    cache_path = tmp_path / "signing_files" / ".token_cache.json"
    cache_path.parent.mkdir()
    cache_path.write_bytes(b"hapsign-token-v1:" + base64.b64encode(b"whatever"))

    assert pipeline._load_token_cache() is None


def test_metadata_does_not_store_keystore_password(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)

    pipeline._save_metadata("key.p12", "cert.cer", "profile.p7b", "team", "oid", "udid")

    metadata = json.loads((tmp_path / "signing" / "metadata.json").read_text("utf-8"))
    assert "keystore_password" not in metadata


def test_metadata_for_another_device_is_ignored(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    paths = {}
    for suffix in ("p12", "cer", "p7b"):
        path = tmp_path / "signing" / f"material.{suffix}"
        path.write_bytes(b"placeholder")
        paths[suffix] = str(path)
    pipeline._udid = "B" * 64
    with open(pipeline._metadata_path, "w", encoding="utf-8") as metadata:
        json.dump(
            {
                "creation_date": date.today().isoformat(),
                "udid": "A" * 64,
                "p12_path": paths["p12"],
                "cer_path": paths["cer"],
                "p7b_path": paths["p7b"],
            },
            metadata,
        )

    assert pipeline._load_cached_metadata() is None


def test_extract_permissions_filters_non_acl_entries(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    allowed = next(iter(ACL_PERMISSION_WHITELIST))
    module_data = {
        "module": {
            "requestPermissions": [
                {"name": allowed},
                {"name": "ohos.permission.INTERNET"},
                {},
            ]
        }
    }
    with zipfile.ZipFile(tmp_path / "app.hap", "w") as archive:
        archive.writestr("module.json", json.dumps(module_data))

    assert pipeline._extract_permissions() == [allowed]


def _pipeline_with_token(tmp_path, monkeypatch) -> SignPipeline:
    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline._token_info = TokenInfo(
        access_token="access",
        refresh_token="refresh",
        user_id="user",
        jwt_token="jwt",
    )
    pipeline._client = Mock()
    monkeypatch.setattr(
        pipeline._token_exchange,
        "refresh_access_token",
        Mock(return_value="new-token"),
    )
    return pipeline


def test_with_refresh_skips_refresh_on_success(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline_with_token(tmp_path, monkeypatch)
    func = Mock(return_value="ok")

    result = pipeline._with_refresh(func, "arg", key="value")

    assert result == "ok"
    func.assert_called_once_with("arg", key="value")
    pipeline._token_exchange.refresh_access_token.assert_not_called()


def test_with_refresh_refreshes_and_retries_on_token_expired(
    tmp_path, monkeypatch
) -> None:
    _fake_dpapi(monkeypatch)
    pipeline = _pipeline_with_token(tmp_path, monkeypatch)
    func = Mock(side_effect=[TokenExpiredError("expired"), "ok"])

    result = pipeline._with_refresh(func)

    assert result == "ok"
    pipeline._token_exchange.refresh_access_token.assert_called_once_with("jwt")
    assert pipeline._client.access_token == "new-token"
    assert pipeline._token_info.access_token == "new-token"
    # 刷新成功后缓存被更新
    assert pipeline._load_token_cache()["access_token"] == "new-token"


def test_with_refresh_propagates_persistent_token_expired(
    tmp_path, monkeypatch
) -> None:
    pipeline = _pipeline_with_token(tmp_path, monkeypatch)
    func = Mock(
        side_effect=[TokenExpiredError("expired"), TokenExpiredError("still expired")]
    )

    with pytest.raises(TokenExpiredError):
        pipeline._with_refresh(func)

    # 仍只刷新一次，重试时再次抛出不再二次刷新
    pipeline._token_exchange.refresh_access_token.assert_called_once_with("jwt")


def test_run_installs_already_signed_hap_directly(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    hap = tmp_path / "signed.hap"
    hap.write_bytes(b"placeholder")
    pipeline = SignPipeline(
        hap_path=str(hap),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
    )
    monkeypatch.setattr(pipeline_module, "is_hap_signed", lambda _path: True)
    install = Mock()
    monkeypatch.setattr(
        pipeline_module,
        "Installer",
        lambda **_kwargs: SimpleNamespace(
            get_udid=lambda: "A" * 64,
            install=install,
            close=Mock(),
        ),
    )
    login = Mock(side_effect=AssertionError("login should be skipped"))
    monkeypatch.setattr(pipeline, "_step_login", login)
    monkeypatch.setattr(pipeline, "_step_sign_hap", Mock())

    assert pipeline.run() is True
    install.assert_called_once_with(str(hap))
    login.assert_not_called()
    pipeline._step_sign_hap.assert_not_called()


def test_sign_only_keeps_signed_hap_without_installing(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    hap = tmp_path / "signed.hap"
    hap.write_bytes(b"placeholder")
    values = []
    pipeline = SignPipeline(
        hap_path=str(hap),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        install_after_sign=False,
        progress_callback=lambda value, label: values.append((value, label)),
    )
    monkeypatch.setattr(pipeline_module, "is_hap_signed", lambda _path: True)
    install = Mock(side_effect=AssertionError("sign must not install"))
    monkeypatch.setattr(
        pipeline_module,
        "Installer",
        lambda **_kwargs: SimpleNamespace(
            get_udid=lambda: "A" * 64,
            install=install,
            close=Mock(),
        ),
    )

    assert pipeline.run() is True
    assert pipeline.signed_hap_path == str(hap)
    assert values[-1] == (100, "签名完成")
    install.assert_not_called()


def test_unsigned_sign_only_omits_install_step(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    pipeline = SignPipeline(
        hap_path=str(tmp_path / "unsigned.hap"),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        install_after_sign=False,
    )
    monkeypatch.setattr(pipeline_module, "is_hap_signed", lambda _path: False)
    monkeypatch.setattr(pipeline, "_step_check_device", Mock())
    monkeypatch.setattr(
        pipeline,
        "_load_cached_metadata",
        Mock(
            return_value={
                "p12_path": "key.p12",
                "cer_path": "cert.cer",
                "p7b_path": "profile.p7b",
            }
        ),
    )
    labels = []

    def _capture_steps(steps):
        labels.extend(label for label, _step in steps)
        return True

    monkeypatch.setattr(pipeline, "_run_steps", _capture_steps)

    assert pipeline._run_pipeline() is True
    assert labels == ["检测设备连接", "签名 hap"]
    assert "安装 hap 到设备" not in labels


def test_run_unsigned_hap_does_not_take_signed_shortcut(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    hap = tmp_path / "unsigned.hap"
    hap.write_bytes(b"placeholder")
    pipeline = SignPipeline(
        hap_path=str(hap),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
    )
    monkeypatch.setattr(pipeline_module, "is_hap_signed", lambda _path: False)
    monkeypatch.setattr(pipeline, "_step_check_device", Mock())
    monkeypatch.setattr(pipeline, "_load_cached_metadata", lambda: None)
    monkeypatch.setattr(pipeline, "_load_token_cache", lambda: None)
    monkeypatch.setattr(
        pipeline,
        "_step_login",
        Mock(side_effect=RuntimeError("stop before network")),
    )

    assert pipeline.run() is False
    pipeline._step_check_device.assert_called_once()
    pipeline._step_login.assert_called_once()


def test_run_stops_before_signing_when_device_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    from hapsign import pipeline as pipeline_module

    pipeline = _pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline_module, "is_hap_signed", Mock())
    monkeypatch.setattr(
        pipeline,
        "_step_check_device",
        Mock(side_effect=RuntimeError("未检测到可用设备")),
    )
    login = Mock()
    monkeypatch.setattr(pipeline, "_step_login", login)

    assert pipeline.run() is False
    pipeline_module.is_hap_signed.assert_not_called()
    login.assert_not_called()


def test_run_always_closes_hdc_installer(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    close = Mock()
    pipeline._installer = SimpleNamespace(close=close)
    monkeypatch.setattr(
        pipeline,
        "_run_pipeline",
        Mock(side_effect=RuntimeError("pipeline failed")),
    )

    with pytest.raises(RuntimeError, match="pipeline failed"):
        pipeline.run()

    close.assert_called_once()
    assert pipeline._installer is None


def test_register_device_reuses_preflight_udid(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline._udid = "B" * 64
    pipeline._team_id = "team"
    pipeline._device_api = SimpleNamespace(
        add_device=Mock(return_value=True),
        find_device_id=Mock(return_value="device-id"),
    )
    get_udid = Mock(side_effect=AssertionError("UDID should be reused"))
    from hapsign import pipeline as pipeline_module

    monkeypatch.setattr(
        pipeline_module,
        "Installer",
        lambda **_kwargs: SimpleNamespace(get_udid=get_udid),
    )

    pipeline._step_register_device()

    get_udid.assert_not_called()
    pipeline._device_api.add_device.assert_called_once()
    assert pipeline._device_id == "device-id"


def test_pipeline_runtime_state_initialized(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)

    assert pipeline._team_id == ""
    assert pipeline._app_info is None
    assert pipeline._cert_result is None
    assert pipeline._p12_path == ""
    assert pipeline._cer_path == ""
    assert pipeline._p7b_path == ""
    assert pipeline._csr_path == ""
    assert pipeline._csr_content == ""
    assert pipeline._signed_hap_path == ""
    assert pipeline.last_error == ""


def test_force_refresh_signing_decision_does_not_leak(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline.force_refresh_token = True
    pipeline.force_refresh_signing = False
    monkeypatch.setattr(pipeline_module, "is_hap_signed", lambda _path: False)
    monkeypatch.setattr(pipeline, "_step_check_device", Mock())
    clear_cache = Mock()
    monkeypatch.setattr(pipeline, "_clear_token_cache", clear_cache)
    load_token_cache = Mock()
    monkeypatch.setattr(pipeline, "_load_token_cache", load_token_cache)
    login = Mock()
    monkeypatch.setattr(pipeline, "_step_login", login)
    exchange = Mock()
    monkeypatch.setattr(pipeline, "_step_exchange_token", exchange)
    monkeypatch.setattr(pipeline, "_run_steps", Mock(return_value=True))

    assert pipeline._run_pipeline() is True

    # 强制刷新 token 走重新登录路径（不再读取 token 缓存）
    clear_cache.assert_called_once()
    login.assert_called_once()
    exchange.assert_called_once()
    load_token_cache.assert_not_called()
    # 局部决策不得泄漏到实例属性：复用实例再次运行不应永久强制刷新签名
    assert pipeline.force_refresh_signing is False
    assert pipeline.force_refresh_token is True


def test_cancelled_pipeline_closes_installer_and_propagates(
    tmp_path, monkeypatch
) -> None:
    cancel_event = threading.Event()
    pipeline = SignPipeline(
        hap_path="app.hap",
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        cancel_event=cancel_event,
    )
    close = Mock()
    pipeline._installer = SimpleNamespace(close=close)
    cancel_event.set()

    with pytest.raises(OperationCancelled):
        pipeline.run()

    close.assert_called_once()
    assert pipeline._installer is None


def test_pipeline_reports_stage_progress(tmp_path, monkeypatch) -> None:
    values = []
    pipeline = SignPipeline(
        hap_path="app.hap",
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        progress_callback=lambda value, label: values.append((value, label)),
    )
    monkeypatch.setattr(pipeline, "_run_pipeline", Mock(return_value=True))

    assert pipeline.run() is True
    assert values[0][0] == 2
    assert values[-1] == (100, "安装完成")


def _prepare_sign_step(pipeline: SignPipeline) -> None:
    pipeline._cer_path = "certificate.cer"
    pipeline._p7b_path = "profile.p7b"
    pipeline._p12_path = "keystore.p12"


def test_kept_signed_hap_preserves_unrelated_hap(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    output_dir = tmp_path / "signed_haps"
    output_dir.mkdir()
    source = output_dir / "new-app.hap"
    source.write_bytes(b"source")
    unrelated = output_dir / "previous_signed.hap"
    unrelated.write_bytes(b"old")
    generated = output_dir / "old-app_signed.hap"
    generated.write_bytes(b"generated-old")
    (output_dir / ".hapsign-signed-haps.json").write_text(
        json.dumps({"version": 1, "generated_haps": [generated.name, source.name]}),
        encoding="utf-8",
    )
    pipeline = SignPipeline(
        hap_path=str(source),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        signed_output_dir=str(output_dir),
        keep_signed_hap=True,
    )
    _prepare_sign_step(pipeline)

    class FakeSigner:
        def __init__(self, **_kwargs) -> None:
            pass

        def sign_hap(self, *_args) -> bool:
            output_path = _args[-1]
            with open(output_path, "wb") as output:
                output.write(b"new")
            return True

    monkeypatch.setattr(pipeline_module, "HapSigner", FakeSigner)

    pipeline._step_sign_hap()

    kept = list(output_dir.glob("*.hap"))
    assert sorted(kept) == sorted(
        [source, unrelated, output_dir / "new-app_signed.hap"]
    )
    assert source.read_bytes() == b"source"
    assert unrelated.read_bytes() == b"old"
    signed_path = output_dir / "new-app_signed.hap"
    assert signed_path.read_bytes() == b"new"
    assert pipeline._signed_hap_path == str(signed_path)
    assert json.loads(
        (output_dir / ".hapsign-signed-haps.json").read_text(encoding="utf-8")
    ) == {"version": 1, "generated_haps": [signed_path.name]}


def test_failed_signing_preserves_previous_output(tmp_path, monkeypatch) -> None:
    from hapsign import pipeline as pipeline_module

    output_dir = tmp_path / "signed_haps"
    output_dir.mkdir()
    old = output_dir / "previous_signed.hap"
    old.write_bytes(b"old")
    pipeline = SignPipeline(
        hap_path=str(tmp_path / "new-app.hap"),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        signed_output_dir=str(output_dir),
        keep_signed_hap=True,
    )
    _prepare_sign_step(pipeline)

    class FailingSigner:
        def __init__(self, **_kwargs) -> None:
            pass

        def sign_hap(self, *_args) -> bool:
            with open(_args[-1], "wb") as output:
                output.write(b"partial")
            raise RuntimeError("signing failed")

    monkeypatch.setattr(pipeline_module, "HapSigner", FailingSigner)

    with pytest.raises(RuntimeError, match="signing failed"):
        pipeline._step_sign_hap()

    assert list(output_dir.glob("*.hap")) == [old]
    assert old.read_bytes() == b"old"


def test_disabled_signed_hap_retention_uses_temporary_file(
    tmp_path, monkeypatch
) -> None:
    from hapsign import pipeline as pipeline_module

    output_dir = tmp_path / "signed_haps"
    pipeline = SignPipeline(
        hap_path=str(tmp_path / "new-app.hap"),
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
        signed_output_dir=str(output_dir),
        keep_signed_hap=False,
    )
    _prepare_sign_step(pipeline)

    class FakeSigner:
        def __init__(self, **_kwargs) -> None:
            pass

        def sign_hap(self, *_args) -> bool:
            with open(_args[-1], "wb") as output:
                output.write(b"temporary")
            return True

    monkeypatch.setattr(pipeline_module, "HapSigner", FakeSigner)

    pipeline._step_sign_hap()
    temporary_path = pipeline._signed_hap_path
    assert os.path.isfile(temporary_path)
    assert not output_dir.exists()

    pipeline._cleanup_temporary_signed_hap()
    assert not os.path.exists(temporary_path)
