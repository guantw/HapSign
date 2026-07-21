"""签名流程中的本地、无网络逻辑测试。"""

import json
import zipfile
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from hapsign.api.client import TokenExpiredError
from hapsign.config import ACL_PERMISSION_WHITELIST
from hapsign.models import TokenInfo
from hapsign.pipeline import SignPipeline


def _pipeline(tmp_path, monkeypatch) -> SignPipeline:
    monkeypatch.chdir(tmp_path)
    return SignPipeline(
        hap_path="app.hap",
        bundle_name="com.example.app",
        work_dir=str(tmp_path / "signing"),
    )


def test_token_cache_round_trip(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)
    pipeline._token_info = TokenInfo(
        access_token="access",
        refresh_token="refresh",
        user_id="user",
        jwt_token="jwt",
    )

    pipeline._save_token_cache()

    assert pipeline._load_token_cache()["access_token"] == "access"


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
            }
        ),
        encoding="utf-8",
    )

    assert pipeline._load_token_cache() is None


def test_metadata_does_not_store_keystore_password(tmp_path, monkeypatch) -> None:
    pipeline = _pipeline(tmp_path, monkeypatch)

    pipeline._save_metadata("key.p12", "cert.cer", "profile.p7b", "team", "oid", "udid")

    metadata = json.loads((tmp_path / "signing" / "metadata.json").read_text("utf-8"))
    assert "keystore_password" not in metadata


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
