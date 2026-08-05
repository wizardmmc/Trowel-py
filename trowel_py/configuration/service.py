"""校验连接组合，并编排配置、catalog、secret 和绑定的原子更新。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from trowel_py.configuration.capabilities import (
    CAPABILITY_REGISTRY_VERSION,
    capability_for,
)
from trowel_py.configuration.catalog import (
    FetchedCatalog,
    HttpModelCatalogFetcher,
    sanitize_url,
)
from trowel_py.configuration.errors import (
    ConfigurationError,
    not_found,
    version_conflict,
)
from trowel_py.configuration.models import (
    CLAUDE_ROLE_NAMES,
    AuthView,
    AgentDefaultsView,
    CapabilityView,
    CatalogView,
    CodexCatalogEntry,
    ConnectionDraft,
    ConnectionKind,
    ConnectionView,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
    SessionConfigurationDraft,
    SessionConfigurationView,
    TaskBindingView,
    TaskId,
)
from trowel_py.configuration.repository import ConfigurationRepository


def _now() -> str:
    """返回可排序且带 UTC 时区的当前时间。"""

    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    """用稳定紧凑格式编码持久化 JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json(value: str | None, fallback: Any) -> Any:
    """解析仓储内部 JSON；损坏时返回不会提升能力的保守值。"""

    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _normalized_url(value: str | None, *, field_name: str) -> str | None:
    """校验 HTTP(S) URL 并拒绝会进入读 API 的 userinfo。"""

    if value is None or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("INVALID_URL", f"{field_name}必须是 HTTP(S) 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError(
            "URL_USERINFO_FORBIDDEN",
            f"{field_name}不能包含用户名或密码，请使用独立 secret 字段",
        )
    if parsed.query or parsed.fragment:
        raise ConfigurationError(
            "INVALID_URL", f"{field_name}不能包含 query 或 fragment"
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ConfigurationError("INVALID_URL", f"{field_name}端口无效") from exc
    return sanitize_url(
        urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, parsed.path.rstrip("/"), "", "")
        )
    )


def _codex_catalog_wire(entries: tuple[CodexCatalogEntry, ...]) -> list[dict[str, Any]]:
    """把 Codex catalog 值对象转换成持久化结构。"""

    return [
        {
            "id": entry.id,
            "display_name": entry.display_name,
            "default_effort": entry.default_effort,
            "supported_efforts": list(entry.supported_efforts),
        }
        for entry in entries
    ]


def _decode_codex_catalog(raw: str) -> tuple[CodexCatalogEntry, ...]:
    """把持久化 catalog 解析为保守值对象。"""

    items = _load_json(raw, [])
    if not isinstance(items, list):
        return ()
    result: list[CodexCatalogEntry] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        efforts = item.get("supported_efforts")
        result.append(
            CodexCatalogEntry(
                id=item["id"],
                display_name=(
                    item.get("display_name")
                    if isinstance(item.get("display_name"), str)
                    else None
                ),
                default_effort=(
                    item.get("default_effort")
                    if isinstance(item.get("default_effort"), str)
                    else None
                ),
                supported_efforts=(
                    tuple(value for value in efforts if isinstance(value, str))
                    if isinstance(efforts, list)
                    else ()
                ),
            )
        )
    return tuple(result)


class ConfigurationService:
    """执行配置领域校验并保持跨表更新的一致性。

    Attributes:
        repository: 当前请求或作业使用的配置仓储。
        catalog_fetcher: 获取第三方模型列表的可替换客户端。
    """

    def __init__(
        self,
        repository: ConfigurationRepository,
        *,
        catalog_fetcher: Any | None = None,
    ) -> None:
        """保存仓储并使用默认 HTTP 模型列表客户端。"""

        self.repository = repository
        self.catalog_fetcher = catalog_fetcher or HttpModelCatalogFetcher()

    def create_connection(self, draft: ConnectionDraft) -> ConnectionView:
        """校验并创建一条缺省为未验证状态的连接。"""

        normalized = self._validate_draft(draft)
        connection_id = str(uuid.uuid4())
        now = _now()
        self.repository.insert_connection(
            {
                "id": connection_id,
                "version": 1,
                "identity_version": 1,
                "name": normalized.name,
                "runtime": normalized.runtime.value,
                "kind": normalized.kind.value,
                "protocol": normalized.protocol.value,
                "base_url": normalized.base_url,
                "models_url": normalized.models_url,
                "auth_kind": self._auth_kind(normalized),
                "login_directory": normalized.login_directory,
                "proxy_url": normalized.proxy_url,
                "proxy_username": normalized.proxy_username,
                "claude_role_models": _json(normalized.claude_role_models),
                "codex_catalog": _json(_codex_catalog_wire(normalized.codex_catalog)),
                "catalog_request_identity": None,
                "catalog_status": "idle",
                "catalog_error_code": None,
                "validation_status": "unknown",
                "capability_version": CAPABILITY_REGISTRY_VERSION,
                "last_session_choice": None,
                "deleted_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_connection(connection_id)

    def get_connection(self, connection_id: str) -> ConnectionView:
        """读取一条未删除连接的脱敏状态。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        return self._connection_view(row)

    def list_connections(self) -> tuple[ConnectionView, ...]:
        """返回所有可供新配置选择的未删除连接。"""

        return tuple(
            self._connection_view(row) for row in self.repository.list_connections()
        )

    def update_connection(
        self,
        connection_id: str,
        *,
        expected_version: int,
        draft: ConnectionDraft,
    ) -> ConnectionView:
        """完整替换非 secret 字段，并按请求身份保留或失效 catalog。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        normalized = self._validate_draft(draft)
        proposed_identity = self._request_identity_for_draft(
            connection_id,
            normalized,
            self.repository.secret_versions(connection_id),
        )
        catalog_identity = (
            normalized.catalog_request_identity or row["catalog_request_identity"]
        )
        stored_catalog = self.repository.get_model_catalog(catalog_identity)
        has_current_catalog = (
            stored_catalog is not None
            and stored_catalog["connection_id"] == connection_id
            and stored_catalog["request_identity"] == proposed_identity
        )
        selected_models = set(normalized.claude_role_models.values()) | {
            item.id for item in normalized.codex_catalog
        }
        if selected_models and not has_current_catalog:
            raise ConfigurationError(
                "CATALOG_STALE",
                "模型映射引用的列表已经过期，请重新获取",
                status_code=409,
            )
        if stored_catalog is not None:
            available = set(_load_json(stored_catalog["models"], []))
            if not selected_models <= available:
                raise ConfigurationError(
                    "MODEL_NOT_IN_CATALOG",
                    "模型映射包含当前列表中不存在的 model ID",
                    status_code=422,
                )
        launch_fields = (
            normalized.runtime.value,
            normalized.kind.value,
            normalized.protocol.value,
            normalized.base_url,
            normalized.login_directory,
            normalized.proxy_url,
            normalized.proxy_username,
            _json(normalized.claude_role_models),
            _json(_codex_catalog_wire(normalized.codex_catalog)),
        )
        previous_launch_fields = (
            row["runtime"],
            row["kind"],
            row["protocol"],
            row["base_url"],
            row["login_directory"],
            row["proxy_url"],
            row["proxy_username"],
            row["claude_role_models"],
            row["codex_catalog"],
        )
        identity_changed = launch_fields != previous_launch_fields
        catalog_identity = proposed_identity if has_current_catalog else None
        self.repository.update_connection(
            connection_id,
            expected_version=expected_version,
            values={
                "version": expected_version + 1,
                "identity_version": int(row["identity_version"])
                + (1 if identity_changed else 0),
                "name": normalized.name,
                "runtime": normalized.runtime.value,
                "kind": normalized.kind.value,
                "protocol": normalized.protocol.value,
                "base_url": normalized.base_url,
                "models_url": normalized.models_url,
                "auth_kind": self._auth_kind(normalized),
                "login_directory": normalized.login_directory,
                "proxy_url": normalized.proxy_url,
                "proxy_username": normalized.proxy_username,
                "claude_role_models": _json(normalized.claude_role_models),
                "codex_catalog": _json(_codex_catalog_wire(normalized.codex_catalog)),
                "catalog_request_identity": catalog_identity,
                "catalog_status": "ready" if catalog_identity else "stale",
                "catalog_error_code": None,
                "validation_status": "unknown",
                "capability_version": CAPABILITY_REGISTRY_VERSION,
                "updated_at": _now(),
            },
        )
        return self.get_connection(connection_id)

    def write_secret(
        self,
        connection_id: str,
        *,
        expected_version: int,
        kind: SecretKind,
        value: str | None,
    ) -> ConnectionView:
        """替换或删除 secret，并使旧 catalog 和启动身份立即失效。"""

        with self.repository.atomic():
            return self._write_secret(
                connection_id,
                expected_version=expected_version,
                kind=kind,
                value=value,
            )

    def _write_secret(
        self,
        connection_id: str,
        *,
        expected_version: int,
        kind: SecretKind,
        value: str | None,
    ) -> ConnectionView:
        """在调用方保存点内执行 secret 与连接身份的多表更新。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        self._validate_secret_kind(row, kind)
        if value is None:
            self.repository.delete_secret(connection_id, kind, _now())
        else:
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError("SECRET_EMPTY", "secret 不能为空")
            self.repository.put_secret(connection_id, kind, value, _now())
        stale = row["catalog_request_identity"] is not None
        self.repository.update_connection(
            connection_id,
            expected_version=expected_version,
            values={
                "version": expected_version + 1,
                "identity_version": int(row["identity_version"]) + 1,
                "catalog_request_identity": None,
                "catalog_status": "stale" if stale else "idle",
                "catalog_error_code": None,
                "claude_role_models": "{}",
                "codex_catalog": "[]",
                "validation_status": "unknown",
                "updated_at": _now(),
            },
        )
        return self.get_connection(connection_id)

    async def fetch_models(
        self,
        connection_id: str,
        *,
        expected_version: int,
        draft: ConnectionDraft | None = None,
    ) -> CatalogView:
        """使用内部 secret 获取模型列表，并拒绝晚到的旧身份结果。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        effective = self._validate_draft(draft) if draft is not None else self._draft(row)
        secret = self.repository.read_secret(connection_id, SecretKind.API_KEY)
        if secret is None:
            raise ConfigurationError("SECRET_MISSING", "连接尚未配置 API key")
        identity = self._request_identity_for_draft(
            connection_id,
            effective,
            self.repository.secret_versions(connection_id),
        )
        try:
            fetched: FetchedCatalog = await self.catalog_fetcher.fetch(
                base_url=effective.base_url or "",
                api_key=secret,
                models_url=effective.models_url,
            )
        except ConfigurationError as exc:
            current = self.repository.get_connection(connection_id)
            if current is not None and int(current["version"]) == expected_version:
                self.repository.update_connection(
                    connection_id,
                    expected_version=expected_version,
                    values={
                        "version": expected_version + 1,
                        "catalog_request_identity": None,
                        "catalog_status": "error",
                        "catalog_error_code": exc.code,
                        "updated_at": _now(),
                    },
                )
                exc.commit_state = True
            raise
        current = self.repository.get_connection(connection_id)
        if current is None or int(current["version"]) != expected_version:
            raise ConfigurationError(
                "STALE_REQUEST",
                "模型列表请求期间连接已经变化，旧结果已丢弃",
                status_code=409,
            )
        current_identity = self._request_identity_for_draft(
            connection_id,
            self._draft(current),
            self.repository.secret_versions(connection_id),
        )
        if draft is None and current_identity != identity:
            raise ConfigurationError(
                "STALE_REQUEST",
                "模型列表请求身份已经变化，旧结果已丢弃",
                status_code=409,
            )
        model_ids = tuple(dict.fromkeys(model.id for model in fetched.models if model.id))
        if not model_ids:
            raise ConfigurationError("EMPTY_CATALOG", "模型服务返回了空列表")
        fetched_at = _now()
        with self.repository.atomic():
            self.repository.put_model_catalog(
                request_identity=identity,
                connection_id=connection_id,
                models=model_ids,
                source_endpoint=sanitize_url(fetched.source_endpoint),
                fetched_at=fetched_at,
            )
            # 草稿身份和当前持久身份相同才把结果提升为连接的当前 catalog；否则只保存
            # 快照，等待 update_connection 用 request identity 原子采纳。
            adopts_now = current_identity == identity
            self.repository.update_connection(
                connection_id,
                expected_version=expected_version,
                values={
                    "version": expected_version + 1,
                    "catalog_request_identity": identity if adopts_now else None,
                    "catalog_status": "ready" if adopts_now else "stale",
                    "catalog_error_code": None,
                    "updated_at": fetched_at,
                },
            )
        return CatalogView(
            status="ready",
            models=model_ids,
            source_endpoint=sanitize_url(fetched.source_endpoint),
            fetched_at=fetched_at,
            request_identity=identity,
            connection_version=expected_version + 1,
        )

    def delete_connection(self, connection_id: str, *, expected_version: int) -> None:
        """软删除连接并永久移除其 secret，保留历史引用。"""

        with self.repository.atomic():
            self._delete_connection(
                connection_id, expected_version=expected_version
            )

    def _delete_connection(
        self, connection_id: str, *, expected_version: int
    ) -> None:
        """在调用方保存点内删除 secret 并软删除连接。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        now = _now()
        self.repository.delete_all_secrets(connection_id, now)
        self.repository.update_connection(
            connection_id,
            expected_version=expected_version,
            values={
                "version": expected_version + 1,
                "identity_version": int(row["identity_version"]) + 1,
                "catalog_request_identity": None,
                "catalog_status": "stale",
                "validation_status": "stale",
                "deleted_at": now,
                "updated_at": now,
            },
        )

    def create_session_configuration(
        self,
        draft: SessionConfigurationDraft,
        *,
        expected_connection_version: int,
    ) -> SessionConfigurationView:
        """只为当前 catalog 中经过真实运行验证的组合创建会话配置。"""

        row, model, capability = self._validate_session_draft(
            draft, expected_connection_version=expected_connection_version
        )
        runtime = RuntimeKind(row["runtime"])
        configuration_id = str(uuid.uuid4())
        now = _now()
        self.repository.insert_session_configuration(
            {
                "id": configuration_id,
                "version": 1,
                "name": draft.name.strip(),
                "runtime": runtime.value,
                "connection_id": draft.connection_id,
                "connection_identity_version": int(row["identity_version"]),
                "model": model,
                "effort": draft.effort,
                "capability_version": capability.version,
                "deleted_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_session_configuration(configuration_id)

    def update_session_configuration(
        self,
        configuration_id: str,
        *,
        expected_version: int,
        expected_connection_version: int,
        draft: SessionConfigurationDraft,
    ) -> SessionConfigurationView:
        """重新校验并完整替换一项会话配置。"""

        current = self.repository.get_session_configuration(configuration_id)
        if current is None:
            raise not_found("会话配置")
        if int(current["version"]) != expected_version:
            raise version_conflict()
        connection, model, capability = self._validate_session_draft(
            draft, expected_connection_version=expected_connection_version
        )
        now = _now()
        self.repository.update_session_configuration(
            configuration_id,
            expected_version=expected_version,
            values={
                "version": expected_version + 1,
                "name": draft.name.strip(),
                "runtime": str(connection["runtime"]),
                "connection_id": draft.connection_id,
                "connection_identity_version": int(connection["identity_version"]),
                "model": model,
                "effort": draft.effort,
                "capability_version": capability.version,
                "updated_at": now,
            },
        )
        return self.get_session_configuration(configuration_id)

    def delete_session_configuration(
        self, configuration_id: str, *, expected_version: int
    ) -> None:
        """软删除会话配置，使已有引用保留但不能用于新任务。"""

        self.repository.update_session_configuration(
            configuration_id,
            expected_version=expected_version,
            values={
                "version": expected_version + 1,
                "deleted_at": _now(),
                "updated_at": _now(),
            },
        )

    def get_session_configuration(
        self, configuration_id: str
    ) -> SessionConfigurationView:
        """读取会话配置并实时降级过期连接或 capability。"""

        row = self.repository.get_session_configuration(configuration_id)
        if row is None:
            raise not_found("会话配置")
        return self._session_view(row)

    def list_session_configurations(self) -> tuple[SessionConfigurationView, ...]:
        """返回设置、Agent 和研讨共用的会话配置 catalog。"""

        return tuple(
            self._session_view(row)
            for row in self.repository.list_session_configurations()
        )

    def put_task_binding(
        self,
        task_id: TaskId,
        *,
        session_configuration_id: str,
        expected_version: int,
    ) -> TaskBindingView:
        """只把任务绑定到当前可用且明确具备该任务资格的配置。"""

        configuration = self.get_session_configuration(session_configuration_id)
        if configuration.availability != "available":
            raise ConfigurationError(
                "SESSION_CONFIGURATION_STALE",
                "会话配置已经过期",
                status_code=409,
            )
        if task_id not in configuration.capability.eligible_tasks:
            raise ConfigurationError(
                "TASK_UNSUPPORTED",
                "该会话配置没有通过这项后台任务的真实运行验证",
                status_code=422,
            )
        version = self.repository.put_task_binding(
            task_id=task_id.value,
            session_configuration_id=session_configuration_id,
            expected_version=expected_version,
            updated_at=_now(),
        )
        return TaskBindingView(task_id, version, session_configuration_id)

    def list_task_bindings(self) -> tuple[TaskBindingView, ...]:
        """返回全部后台任务绑定。"""

        return tuple(
            TaskBindingView(
                TaskId(row["task_id"]),
                int(row["version"]),
                (
                    str(row["session_configuration_id"])
                    if row["session_configuration_id"] is not None
                    else None
                ),
            )
            for row in self.repository.list_task_bindings()
        )

    def delete_task_binding(
        self, task_id: TaskId, *, expected_version: int
    ) -> TaskBindingView:
        """解除一项后台任务绑定并保留单调版本。"""

        version = self.repository.delete_task_binding(
            task_id.value,
            expected_version=expected_version,
            updated_at=_now(),
        )
        return TaskBindingView(task_id, version, None)

    def get_agent_defaults(self) -> AgentDefaultsView:
        """读取 Agent 默认条件；未保存时返回现有产品的兼容默认值。"""

        row = self.repository.get_agent_defaults()
        if row is None:
            return AgentDefaultsView(0, None, None, True, True, True)
        return AgentDefaultsView(
            version=int(row["version"]),
            session_configuration_id=(
                str(row["session_configuration_id"])
                if row["session_configuration_id"] is not None
                else None
            ),
            permission=(
                str(row["permission"]) if row["permission"] is not None else None
            ),
            memory_enabled=bool(row["memory_enabled"]),
            profile_enabled=bool(row["profile_enabled"]),
            self_enabled=bool(row["self_enabled"]),
        )

    def put_agent_defaults(
        self,
        *,
        expected_version: int,
        session_configuration_id: str | None,
        permission: str | None,
        memory_enabled: bool,
        profile_enabled: bool,
        self_enabled: bool,
    ) -> AgentDefaultsView:
        """保存只影响之后新建会话的 Agent 默认条件。"""

        if session_configuration_id is not None:
            configuration = self.get_session_configuration(session_configuration_id)
            if configuration.availability != "available":
                raise ConfigurationError(
                    "SESSION_CONFIGURATION_STALE",
                    "默认会话配置已经过期",
                    status_code=409,
                )
            if configuration.runtime == RuntimeKind.DIRECT_API:
                raise ConfigurationError(
                    "AGENT_RUNTIME_REQUIRED",
                    "Agent 默认配置必须使用可创建会话的 runtime",
                )
        self.repository.put_agent_defaults(
            expected_version=expected_version,
            session_configuration_id=session_configuration_id,
            permission=permission,
            memory_enabled=memory_enabled,
            profile_enabled=profile_enabled,
            self_enabled=self_enabled,
            updated_at=_now(),
        )
        return self.get_agent_defaults()

    def delete_agent_defaults(self, *, expected_version: int) -> AgentDefaultsView:
        """重置显式默认条件并保留单调版本。"""

        self.repository.delete_agent_defaults(
            expected_version=expected_version, updated_at=_now()
        )
        return self.get_agent_defaults()

    def record_last_session_choice(
        self,
        connection_id: str,
        *,
        expected_version: int,
        model: str,
        effort: str | None,
    ) -> ConnectionView:
        """在 runtime 确认会话创建成功后记录最近选择。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        self.repository.update_connection(
            connection_id,
            expected_version=expected_version,
            values={
                "version": expected_version + 1,
                "last_session_choice": _json({"model": model, "effort": effort}),
                "updated_at": _now(),
            },
        )
        return self.get_connection(connection_id)

    def _validate_session_draft(
        self,
        draft: SessionConfigurationDraft,
        *,
        expected_connection_version: int,
    ) -> tuple[sqlite3.Row, str, CapabilityView]:
        """返回通过当前 catalog 和 capability 门禁的连接、模型与能力。"""

        row = self.repository.get_connection(draft.connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_connection_version:
            raise version_conflict()
        catalog = self._catalog_view(row)
        if catalog.status != "ready":
            raise ConfigurationError(
                "CATALOG_STALE", "连接的模型列表尚未准备好", status_code=409
            )
        model = draft.model.strip()
        if len(model) > 512:
            raise ConfigurationError("MODEL_ID_INVALID", "会话 model ID 无效")
        if draft.effort is not None and len(draft.effort) > 32:
            raise ConfigurationError("EFFORT_INVALID", "思考强度字段无效")
        if not draft.name.strip():
            raise ConfigurationError("NAME_REQUIRED", "会话配置名称不能为空")
        if len(draft.name.strip()) > 120:
            raise ConfigurationError("NAME_INVALID", "会话配置名称过长")
        if model not in catalog.models:
            raise ConfigurationError(
                "MODEL_NOT_IN_CATALOG", "所选模型不在当前模型列表中", status_code=422
            )
        capability = capability_for(
            RuntimeKind(row["runtime"]),
            ConnectionKind(row["kind"]),
            ProtocolKind(row["protocol"]),
            model,
            draft.effort,
        )
        if capability.status != "verified":
            raise ConfigurationError(
                f"CAPABILITY_{capability.status.upper()}",
                "这项 runtime、连接和模型组合尚未通过真实能力验证",
                status_code=422,
            )
        return row, model, capability

    def _validate_draft(self, draft: ConnectionDraft) -> ConnectionDraft:
        """校验 runtime 专属字段并返回规范化草稿。"""

        name = draft.name.strip()
        if not name:
            raise ConfigurationError("NAME_REQUIRED", "连接名称不能为空")
        if len(name) > 120:
            raise ConfigurationError("NAME_INVALID", "连接名称过长")
        expected: dict[ConnectionKind, tuple[RuntimeKind, ProtocolKind]] = {
            ConnectionKind.CLAUDE_COMPATIBLE: (
                RuntimeKind.CLAUDE_CODE,
                ProtocolKind.ANTHROPIC_MESSAGES,
            ),
            ConnectionKind.CODEX_OFFICIAL: (
                RuntimeKind.CODEX,
                ProtocolKind.CODEX_OFFICIAL,
            ),
            ConnectionKind.CODEX_CUSTOM: (
                RuntimeKind.CODEX,
                ProtocolKind.OPENAI_RESPONSES,
            ),
        }
        if draft.kind == ConnectionKind.DIRECT_API:
            if draft.runtime != RuntimeKind.DIRECT_API or draft.protocol not in {
                ProtocolKind.ANTHROPIC_MESSAGES,
                ProtocolKind.OPENAI_RESPONSES,
            }:
                raise ConfigurationError(
                    "CONNECTION_SHAPE_INVALID", "direct API 的 runtime 或协议不匹配"
                )
        elif expected[draft.kind] != (draft.runtime, draft.protocol):
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "连接种类与 runtime 或协议不匹配"
            )
        base_url = _normalized_url(draft.base_url, field_name="模型服务地址")
        models_url = _normalized_url(draft.models_url, field_name="模型列表地址")
        proxy_url = _normalized_url(draft.proxy_url, field_name="代理地址")
        login_directory = (
            str(Path(draft.login_directory).expanduser().resolve())
            if draft.login_directory and draft.login_directory.strip()
            else None
        )
        if draft.kind == ConnectionKind.CODEX_OFFICIAL:
            if base_url is not None or models_url is not None or login_directory is None:
                raise ConfigurationError(
                    "CONNECTION_SHAPE_INVALID",
                    "Codex official 只接受原生登录目录引用，不接受模型服务地址",
                )
        elif base_url is None or login_directory is not None:
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "自定义连接必须提供模型服务地址"
            )
        unknown_roles = set(draft.claude_role_models) - CLAUDE_ROLE_NAMES
        if unknown_roles:
            raise ConfigurationError(
                "CLAUDE_ROLE_UNKNOWN", "Claude 角色映射包含未知角色"
            )
        if (
            draft.kind != ConnectionKind.CLAUDE_COMPATIBLE
            and draft.claude_role_models
        ):
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "只有 Claude Code 连接可以保存角色映射"
            )
        if draft.kind != ConnectionKind.CODEX_CUSTOM and draft.codex_catalog:
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "只有 Codex 第三方连接可以保存模型目录"
            )
        if any(
            not model.strip() or len(model.strip()) > 512
            for model in draft.claude_role_models.values()
        ):
            raise ConfigurationError("MODEL_ID_INVALID", "Claude 角色 model ID 无效")
        if any(len(entry.id.strip()) > 512 for entry in draft.codex_catalog):
            raise ConfigurationError("MODEL_ID_INVALID", "Codex model ID 无效")
        codex_ids = [entry.id.strip() for entry in draft.codex_catalog]
        if len(set(codex_ids)) != len(codex_ids):
            raise ConfigurationError("MODEL_ID_DUPLICATE", "Codex model ID 不能重复")
        if any(
            (entry.display_name is not None and len(entry.display_name) > 120)
            or (entry.default_effort is not None and len(entry.default_effort) > 32)
            or len(entry.supported_efforts) > 16
            or any(len(effort) > 32 for effort in entry.supported_efforts)
            for entry in draft.codex_catalog
        ):
            raise ConfigurationError("CODEX_CATALOG_INVALID", "Codex 模型元数据无效")
        proxy_username = (draft.proxy_username or "").strip() or None
        if proxy_username is not None and proxy_url is None:
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "代理用户名必须和代理地址一起配置"
            )
        return replace(
            draft,
            name=name,
            base_url=base_url,
            models_url=models_url,
            login_directory=login_directory,
            proxy_url=proxy_url,
            proxy_username=proxy_username,
            claude_role_models={
                role: model.strip()
                for role, model in draft.claude_role_models.items()
                if model.strip()
            },
            codex_catalog=tuple(
                replace(entry, id=entry.id.strip())
                for entry in draft.codex_catalog
                if entry.id.strip()
            ),
        )

    def _auth_kind(self, draft: ConnectionDraft) -> str:
        """返回连接种类决定的认证类型。"""

        return (
            "oauth_reference"
            if draft.kind == ConnectionKind.CODEX_OFFICIAL
            else "api_key"
        )

    def _validate_secret_kind(self, row: sqlite3.Row, kind: SecretKind) -> None:
        """拒绝给 Codex official 写 API key 或无代理时写代理密码。"""

        if kind == SecretKind.API_KEY and row["auth_kind"] != "api_key":
            raise ConfigurationError(
                "SECRET_KIND_UNSUPPORTED", "这类连接不保存 API key"
            )
        if kind == SecretKind.PROXY_PASSWORD and not row["proxy_url"]:
            raise ConfigurationError(
                "SECRET_KIND_UNSUPPORTED", "连接尚未配置需要认证的代理"
            )

    def _request_identity_for_draft(
        self,
        connection_id: str,
        draft: ConnectionDraft,
        secret_versions: dict[str, int],
    ) -> str:
        """计算包含连接隔离边界且不含 secret 原值的模型列表请求身份哈希。"""

        payload = {
            "connection_id": connection_id,
            "runtime": draft.runtime.value,
            "protocol": draft.protocol.value,
            "base_url": draft.base_url,
            "models_url": draft.models_url,
            "api_key_version": secret_versions.get(SecretKind.API_KEY.value, 0),
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    def _draft(self, row: sqlite3.Row) -> ConnectionDraft:
        """把连接持久事实恢复为不含 secret 的编辑草稿。"""

        return ConnectionDraft(
            name=str(row["name"]),
            runtime=RuntimeKind(row["runtime"]),
            kind=ConnectionKind(row["kind"]),
            protocol=ProtocolKind(row["protocol"]),
            base_url=row["base_url"],
            models_url=row["models_url"],
            login_directory=row["login_directory"],
            proxy_url=row["proxy_url"],
            proxy_username=row["proxy_username"],
            claude_role_models=_load_json(row["claude_role_models"], {}),
            codex_catalog=_decode_codex_catalog(row["codex_catalog"]),
            catalog_request_identity=row["catalog_request_identity"],
        )

    def _catalog_view(self, row: sqlite3.Row) -> CatalogView:
        """只为 ready 请求身份返回模型列表内容。"""

        status = str(row["catalog_status"])
        snapshot = self.repository.get_model_catalog(row["catalog_request_identity"])
        if status != "ready" or snapshot is None:
            return CatalogView(
                status=status,
                error_code=row["catalog_error_code"],
                connection_version=int(row["version"]),
            )
        models = _load_json(snapshot["models"], [])
        return CatalogView(
            status="ready",
            models=tuple(model for model in models if isinstance(model, str)),
            source_endpoint=sanitize_url(str(snapshot["source_endpoint"])),
            fetched_at=str(snapshot["fetched_at"]),
            request_identity=str(snapshot["request_identity"]),
            connection_version=int(row["version"]),
        )

    def _connection_view(self, row: sqlite3.Row) -> ConnectionView:
        """把 SQLite 行转换成唯一的脱敏连接读模型。"""

        connection_id = str(row["id"])
        secret_versions = self.repository.secret_versions(connection_id)
        configured_secrets = self.repository.configured_secret_kinds(connection_id)
        auth_status = (
            "referenced"
            if row["auth_kind"] == "oauth_reference"
            else (
                "configured"
                if SecretKind.API_KEY.value in configured_secrets
                else "missing"
            )
        )
        base_url = row["base_url"]
        upstream_host = urlsplit(base_url).hostname if base_url else None
        login_directory = row["login_directory"]
        catalog = self._catalog_view(row)
        role_models = _load_json(row["claude_role_models"], {})
        if not isinstance(role_models, dict):
            role_models = {}
        last_choice = _load_json(row["last_session_choice"], None)
        return ConnectionView(
            id=str(row["id"]),
            version=int(row["version"]),
            identity_version=int(row["identity_version"]),
            name=str(row["name"]),
            runtime=RuntimeKind(row["runtime"]),
            kind=ConnectionKind(row["kind"]),
            protocol=ProtocolKind(row["protocol"]),
            base_url=sanitize_url(str(base_url)) if base_url else None,
            models_url=(
                sanitize_url(str(row["models_url"])) if row["models_url"] else None
            ),
            upstream_host=upstream_host,
            auth=AuthView(str(row["auth_kind"]), auth_status),
            login_directory=str(login_directory) if login_directory else None,
            login_directory_exists=(
                Path(login_directory).expanduser().exists() if login_directory else None
            ),
            proxy_url=(
                sanitize_url(str(row["proxy_url"])) if row["proxy_url"] else None
            ),
            proxy_username=(
                str(row["proxy_username"]) if row["proxy_username"] else None
            ),
            proxy_password_status=(
                "configured"
                if SecretKind.PROXY_PASSWORD.value in configured_secrets
                else "missing"
            ),
            claude_role_models={str(k): str(v) for k, v in role_models.items()},
            codex_catalog=_decode_codex_catalog(row["codex_catalog"]),
            catalog=catalog,
            validation_status=str(row["validation_status"]),
            capability_version=str(row["capability_version"]),
            last_session_choice=(
                last_choice if isinstance(last_choice, dict) else None
            ),
            secret_versions=secret_versions,
            preview=self._preview(row, auth_status),
        )

    def _preview(self, row: sqlite3.Row, auth_status: str) -> dict[str, Any]:
        """由持久事实生成只读脱敏 JSON/TOML 预览。"""

        if row["kind"] == ConnectionKind.CLAUDE_COMPATIBLE.value:
            body = {
                "env": {
                    "ANTHROPIC_BASE_URL": row["base_url"],
                    "ANTHROPIC_AUTH_TOKEN": f"<{auth_status}>",
                },
                "model_roles": _load_json(row["claude_role_models"], {}),
            }
            return {"format": "json", "body": body}
        if row["kind"] == ConnectionKind.CODEX_OFFICIAL.value:
            return {
                "format": "toml",
                "body": {
                    "provider": "openai",
                    "login_directory": row["login_directory"],
                    "oauth": "<native reference>",
                },
            }
        return {
            "format": "toml",
            "body": {
                "model_provider": row["id"],
                "base_url": row["base_url"],
                "wire_api": row["protocol"],
                "api_key": f"<{auth_status}>",
                "models": _load_json(row["codex_catalog"], []),
            },
        }

    def _session_view(self, row: sqlite3.Row) -> SessionConfigurationView:
        """把持久会话配置与当前连接和能力表重新核对。"""

        connection = self.repository.get_connection(
            str(row["connection_id"]), include_deleted=True
        )
        runtime = RuntimeKind(row["runtime"])
        if connection is None:
            capability = capability_for(
                runtime,
                ConnectionKind.CODEX_CUSTOM,
                ProtocolKind.OPENAI_RESPONSES,
                str(row["model"]),
                row["effort"],
            )
            availability = "stale"
            reason = "connection_missing"
        else:
            capability = capability_for(
                runtime,
                ConnectionKind(connection["kind"]),
                ProtocolKind(connection["protocol"]),
                str(row["model"]),
                row["effort"],
            )
            catalog = self._catalog_view(connection)
            if connection["deleted_at"] is not None:
                availability, reason = "stale", "connection_deleted"
            elif int(connection["identity_version"]) != int(
                row["connection_identity_version"]
            ):
                availability, reason = "stale", "connection_identity_changed"
            elif row["capability_version"] != CAPABILITY_REGISTRY_VERSION:
                availability, reason = "stale", "capability_version_changed"
            elif capability.status != "verified":
                availability, reason = "stale", "capability_unavailable"
            elif catalog.status != "ready":
                availability, reason = "stale", "catalog_not_ready"
            elif str(row["model"]) not in catalog.models:
                availability, reason = "stale", "model_not_in_catalog"
            else:
                availability, reason = "available", None
        return SessionConfigurationView(
            id=str(row["id"]),
            version=int(row["version"]),
            name=str(row["name"]),
            runtime=runtime,
            connection_id=str(row["connection_id"]),
            connection_identity_version=int(row["connection_identity_version"]),
            model=str(row["model"]),
            effort=str(row["effort"]) if row["effort"] is not None else None,
            capability=capability,
            availability=availability,
            disabled_reason=reason,
        )
