"""把旧 ``[llm].active`` 幂等复制为 Trowel 自有 direct API 连接。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

from trowel_py.configuration.capabilities import CAPABILITY_REGISTRY_VERSION
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.models import (
    ConnectionDraft,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
)
from trowel_py.configuration.repository import ConfigurationRepository
from trowel_py.configuration.service import ConfigurationService, _now

_MIGRATION_KEY = "legacy_llm_active_v1"


@dataclass(frozen=True)
class LegacyMigrationResult:
    """记录旧模型配置迁移是否导入、跳过或拒绝。

    Attributes:
        status: imported、already_imported、missing 或 invalid。
        connection_id: 成功导入的稳定连接 ID；其他状态为 None。
    """

    status: str
    connection_id: str | None = None


def migrate_legacy_llm_config(
    repository: ConfigurationRepository,
    config_path: Path,
) -> LegacyMigrationResult:
    """在一个 SQLite 事务中复制旧 active 配置和 API key。

    源文件始终保留不动。读取、字段校验或落库失败时回滚全部新增行，并返回
    ``invalid``；已经写入迁移标记时不再读取可能变化的源文件。

    Args:
        repository: 与 Trowel 主库同事务的配置仓储。
        config_path: 旧 ``config.toml`` 的明确路径。
    """

    if repository.get_meta(_MIGRATION_KEY) is not None:
        return LegacyMigrationResult("already_imported")
    if not config_path.is_file():
        return LegacyMigrationResult("missing")
    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
        llm = payload["llm"]
        active = llm["active"]
        selected = llm[active]
        provider = selected["provider"]
        model = selected["model"]
        api_key = selected["api_key"]
        base_url = selected["base_url"]
        if not all(
            isinstance(value, str) and value.strip()
            for value in (active, provider, model, api_key, base_url)
        ):
            raise ValueError("legacy llm fields are invalid")
        protocol = {
            "anthropic": ProtocolKind.ANTHROPIC_MESSAGES,
            "openai": ProtocolKind.OPENAI_RESPONSES,
        }[provider]
        draft = ConnectionDraft(
            name=active,
            runtime=RuntimeKind.DIRECT_API,
            kind=ConnectionKind.DIRECT_API,
            protocol=protocol,
            base_url=base_url,
        )
        service = ConfigurationService(repository)
        with repository.connection:
            created = service.create_connection(draft)
            updated = service.write_secret(
                created.id,
                expected_version=created.version,
                kind=SecretKind.API_KEY,
                value=api_key,
            )
            # 旧配置里的 model 是当前使用事实，不是上游模型列表证据。保留一个 stale
            # catalog 状态供设置页提示重新获取，不能据此提升 capability。
            repository.update_connection(
                created.id,
                expected_version=updated.version,
                values={
                    "version": updated.version + 1,
                    "catalog_status": "stale",
                    "catalog_error_code": "LEGACY_REQUIRES_REFRESH",
                    "validation_status": "unknown",
                    "capability_version": CAPABILITY_REGISTRY_VERSION,
                    "last_session_choice": json.dumps(
                        {"effort": None, "model": model},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "updated_at": _now(),
                },
            )
            fingerprint = hashlib.sha256(
                f"{active}\0{provider}\0{model}\0{base_url}".encode("utf-8")
            ).hexdigest()
            repository.put_meta(_MIGRATION_KEY, fingerprint, _now())
        return LegacyMigrationResult("imported", created.id)
    except (
        ConfigurationError,
        KeyError,
        OSError,
        sqlite3.Error,
        tomllib.TOMLDecodeError,
        TypeError,
        ValueError,
    ):
        repository.connection.rollback()
        return LegacyMigrationResult("invalid")
