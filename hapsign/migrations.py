"""面向用户和 agent 的兼容性变更目录与缓存迁移工具。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path

from hapsign.diagnostics import is_valid_device_udid

LEGACY_CACHE_CHANGE_ID = "HAPSIGN-BREAKING-001"
LEGACY_STATE_CHANGE_ID = "HAPSIGN-BREAKING-003"

BREAKING_CHANGES: tuple[dict[str, object], ...] = (
    {
        "id": LEGACY_CACHE_CHANGE_ID,
        "introduced_in": "unreleased",
        "destructive": True,
        "decision": "accepted",
        "compatibility_strategy": "configuration-and-explicit-migration",
        "compatibility_options": (
            "--enable-capability",
            "migrate-cache",
            "backup-and-refresh",
        ),
        "summary": "旧签名缓存不满足新的安全一致性校验时不再自动复用",
        "impact": (
            "缺少或切换能力模式、包名不匹配或设备 UDID 无效时会重新申请材料；"
            "生成同名密钥库时会替换本地 .p12，"
            "申请证书时可能删除并替换远端同名调试证书。"
        ),
        "remediation": (
            "让 inspect 与 sign/deploy 使用一致的 --enable-capability；缺少能力模式时"
            "可在确认旧 Profile 类型后运行 migrate-cache，否则先备份再允许刷新。"
        ),
        "docs": "docs/MIGRATIONS.md#hapsign-breaking-001",
    },
    {
        "id": "HAPSIGN-BREAKING-002",
        "introduced_in": "unreleased",
        "destructive": False,
        "decision": "accepted",
        "compatibility_strategy": "configuration",
        "compatibility_options": ("--browser system", "HAPSIGN_BROWSER=system"),
        "summary": "CLI 默认浏览器由 system 改为 system_controlled",
        "impact": "授权使用隔离的 Edge/Chrome 上下文，不复用默认浏览器的 cookie。",
        "remediation": (
            "需要原行为时传入 --browser system，或设置 HAPSIGN_BROWSER=system。"
        ),
        "docs": "docs/MIGRATIONS.md#hapsign-breaking-002",
    },
    {
        "id": "HAPSIGN-BREAKING-003",
        "introduced_in": "unreleased",
        "destructive": True,
        "decision": "accepted",
        "compatibility_strategy": "configuration",
        "compatibility_options": (
            "--state-dir/--output-dir",
            "HAPSIGN_SIGNING_DIR/HAPSIGN_SIGNED_HAPS_DIR",
        ),
        "summary": "CLI 默认状态和产物改用应用配置目录",
        "impact": (
            "PR #5 使用的 ~/.hapsign 及更早版本工作目录中的缓存不会自动搬迁，"
            "默认产物位置也会变化；继续签名可能重新生成密钥并替换远端同名调试证书。"
        ),
        "remediation": (
            "使用 --state-dir/--output-dir 恢复原路径，或通过 HAPSIGN_SIGNING_DIR/"
            "HAPSIGN_SIGNED_HAPS_DIR 持久配置。"
        ),
        "docs": "docs/MIGRATIONS.md#hapsign-breaking-003",
    },
    {
        "id": "HAPSIGN-BREAKING-004",
        "introduced_in": "unreleased",
        "destructive": False,
        "decision": "accepted",
        "compatibility_strategy": "migration-only",
        "compatibility_options": ("--json with stderr diagnostics",),
        "summary": "CLI 普通输出和日志流已分离",
        "impact": "依赖旧标准输出日志文本的脚本需要调整解析方式。",
        "remediation": "自动化调用应使用 --json，并按退出码及 ok 字段判断结果。",
        "docs": "docs/MIGRATIONS.md#hapsign-breaking-004",
    },
    {
        "id": "HAPSIGN-BREAKING-005",
        "introduced_in": "unreleased",
        "destructive": False,
        "decision": "accepted",
        "compatibility_strategy": "migration-only",
        "compatibility_options": ("explicit subcommands",),
        "summary": "CLI 改为显式子命令接口",
        "impact": (
            "旧的 hapsign --hap ...、--doctor、--inspect 和 --sign-only 调用不再解析。"
        ),
        "remediation": (
            "改用 doctor、inspect、migrate-cache、sign、deploy 或 install 子命令；"
            "设备安装命令必须显式传入 --serial。"
        ),
        "docs": "docs/MIGRATIONS.md#hapsign-breaking-005",
    },
)


def breaking_changes() -> list[dict[str, object]]:
    """返回可安全序列化且可由调用方修改的兼容性变更目录。"""
    return [dict(change) for change in BREAKING_CHANGES]


def _existing_artifact_paths(
    metadata_path: Path, metadata: dict[str, object]
) -> dict[str, Path] | None:
    """解析旧元数据路径；默认材料都与 metadata.json 位于同一目录。"""
    artifacts: dict[str, Path] = {}
    for key in ("p12_path", "cer_path", "p7b_path"):
        raw_path = metadata.get(key)
        if not isinstance(raw_path, str) or not raw_path:
            return None
        configured = Path(raw_path).expanduser()
        candidates = [configured] if configured.is_absolute() else []
        candidates.append(metadata_path.parent / configured.name)
        match = next(
            (candidate.resolve() for candidate in candidates if candidate.is_file()),
            None,
        )
        if match is None:
            return None
        artifacts[key] = match
    return artifacts


def cache_compatibility_warning(
    metadata_path: Path,
    *,
    bundle_name: str = "",
    enable_capability: bool = False,
) -> dict[str, object] | None:
    """检查现有元数据是否会因本次目标模式触发破坏性材料刷新。"""
    if not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    if metadata.get("creation_date") != date.today().isoformat():
        return None
    if _existing_artifact_paths(metadata_path, metadata) is None:
        return None
    reasons: list[str] = []
    cached_capability = metadata.get("enable_capability")
    cached_request = metadata.get("requested_enable_capability", cached_capability)
    if not isinstance(cached_capability, bool):
        reasons.append("missing_capability_mode")
    elif not isinstance(cached_request, bool):
        reasons.append("missing_requested_capability_mode")
    elif cached_capability and not cached_request:
        reasons.append("invalid_effective_capability_mode")
    elif cached_request != enable_capability:
        reasons.append("capability_mode_mismatch")
    if bundle_name and metadata.get("bundle_name") != bundle_name:
        reasons.append("bundle_name_mismatch")
    if not is_valid_device_udid(metadata.get("udid")):
        reasons.append("invalid_device_udid")
    if not reasons:
        return None
    migratable = reasons == ["missing_capability_mode"]
    catalog_entry = next(
        change for change in BREAKING_CHANGES if change["id"] == LEGACY_CACHE_CHANGE_ID
    )
    warning = dict(catalog_entry)
    warning["metadata"] = str(metadata_path.expanduser().absolute())
    warning["applicable"] = True
    warning["requires_user_decision"] = True
    warning["reasons"] = reasons
    warning["migratable"] = migratable
    warning["expected_capability_mode"] = (
        "system-basic" if enable_capability else "normal"
    )
    warning["cached_capability_mode"] = (
        "system-basic"
        if cached_request is True
        else "normal"
        if cached_request is False
        else "unknown"
    )
    warning["cached_effective_capability_mode"] = (
        "system-basic"
        if cached_capability is True
        else "normal"
        if cached_capability is False
        else "unknown"
    )
    if reasons == ["capability_mode_mismatch"]:
        matching_option = (
            "传入 --enable-capability"
            if cached_request is True
            else "不要传入 --enable-capability"
        )
        warning["remediation"] = (
            f"现有缓存模式为 {warning['cached_capability_mode']}；若要复用，"
            f"inspect 和 sign/deploy 应一致地{matching_option}。"
            "若确实要切换模式，先备份该 bundle 的整个签名目录，再允许刷新。"
        )
    elif not migratable:
        warning["remediation"] = (
            "先备份该 bundle 的整个签名目录；缓存还存在请求能力模式、包名或设备 "
            "UDID 一致性问题，不能通过 migrate-cache 安全复用，只能在确认后允许刷新。"
        )
    return warning


def legacy_state_warning(
    state_dir: Path,
    work_dir: Path,
    *,
    bundle_name: str,
    legacy_state_dir: Path | None = None,
) -> dict[str, object] | None:
    """检测 PR #5 用户主目录状态是否会被新的应用目录默认值遗漏。"""
    selected_state = state_dir.expanduser().resolve()
    selected_work = work_dir.expanduser().resolve()
    legacy_state = (
        legacy_state_dir.expanduser().resolve()
        if legacy_state_dir is not None
        else (Path.home() / ".hapsign").resolve()
    )
    if selected_state == legacy_state:
        return None

    found: list[str] = []
    selected_token = selected_state / ".token_cache.json"
    legacy_token = legacy_state / ".token_cache.json"
    if not selected_token.is_file() and legacy_token.is_file():
        found.append("token_cache")

    selected_metadata = selected_work / "metadata.json"
    legacy_metadata = legacy_state / bundle_name / "metadata.json"
    if not selected_metadata.is_file() and legacy_metadata.is_file():
        try:
            metadata = json.loads(legacy_metadata.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            metadata = None
        if (
            isinstance(metadata, dict)
            and metadata.get("creation_date") == date.today().isoformat()
            and metadata.get("bundle_name") == bundle_name
            and _existing_artifact_paths(legacy_metadata, metadata) is not None
        ):
            found.append("signing_materials")

    if not found:
        return None
    catalog_entry = next(
        change for change in BREAKING_CHANGES if change["id"] == LEGACY_STATE_CHANGE_ID
    )
    warning = dict(catalog_entry)
    destructive = "signing_materials" in found
    warning.update(
        {
            "applicable": True,
            "destructive": destructive,
            "requires_user_decision": destructive,
            "legacy_state_dir": str(legacy_state),
            "selected_state_dir": str(selected_state),
            "found": found,
        }
    )
    return warning


def migrate_legacy_cache(
    metadata_path: Path,
    *,
    bundle_name: str,
    enable_capability: bool,
) -> dict[str, object]:
    """在用户明确选择能力模式后原子补齐旧缓存元数据。"""
    resolved = metadata_path.expanduser().resolve()
    try:
        metadata = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"未找到旧缓存元数据：{resolved}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"旧缓存元数据无法读取：{resolved}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"旧缓存元数据不是 JSON 对象：{resolved}")
    if metadata.get("bundle_name") != bundle_name:
        raise ValueError("旧缓存元数据的 bundle_name 与输入 HAP 不匹配")
    if metadata.get("creation_date") != date.today().isoformat():
        raise ValueError("旧缓存不是当天创建的，无法迁移复用；请备份后允许刷新")
    if not is_valid_device_udid(metadata.get("udid")):
        raise ValueError("旧缓存缺少有效的 64 位设备 UDID，无法迁移安全复用")

    artifacts = _existing_artifact_paths(resolved, metadata)
    if artifacts is None:
        raise ValueError("旧缓存签名材料缺失，无法迁移复用；请备份后允许刷新")

    existing = metadata.get("enable_capability")
    if isinstance(existing, bool):
        if existing != enable_capability:
            raise ValueError("缓存已声明其他能力模式；为避免误用 Profile，不会自动覆盖")
        requested = metadata.get("requested_enable_capability", existing)
        if not isinstance(requested, bool):
            raise ValueError("缓存声明的请求能力模式无效，无法迁移安全复用")
        if existing and not requested:
            raise ValueError("缓存的实际能力模式高于请求模式，无法迁移安全复用")
    else:
        requested = enable_capability
    normalized_artifacts = {key: str(path) for key, path in artifacts.items()}
    changed = (
        not isinstance(existing, bool)
        or metadata.get("requested_enable_capability") != requested
        or any(
            metadata.get(key) != value for key, value in normalized_artifacts.items()
        )
    )
    if not changed:
        return {
            "changed": False,
            "metadata": str(resolved),
            "backup": "",
            "enable_capability": enable_capability,
        }

    backup = resolved.with_name(resolved.name + ".pre-capability-migration.bak")
    if not backup.exists():
        shutil.copy2(resolved, backup)

    metadata["enable_capability"] = enable_capability
    metadata["requested_enable_capability"] = requested
    metadata.update(normalized_artifacts)
    temporary = resolved.with_name(resolved.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, resolved.stat().st_mode)
        except OSError:
            pass
        os.replace(temporary, resolved)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    return {
        "changed": True,
        "metadata": str(resolved),
        "backup": str(backup),
        "enable_capability": enable_capability,
    }
