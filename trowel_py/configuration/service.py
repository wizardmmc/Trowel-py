"""校验连接组合，并编排配置、catalog、secret 和绑定的原子更新。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from trowel_py.configuration.capabilities import (
    CAPABILITY_REGISTRY_VERSION,
    capability_for,
)
from trowel_py.configuration.catalog import (
    FetchedCatalog,
    HttpModelCatalogFetcher,
    sanitize_url,
)
from trowel_py.configuration.claude_home import (
    ClaudeConnectionHomeError,
    ClaudeConnectionHomeStore,
    ClaudeHomeTombstone,
)
from trowel_py.configuration.codex_home import (
    CodexConnectionHomeError,
    CodexConnectionHomeStore,
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
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from trowel_py.application_paths import resolve_application_data_root

_CLAUDE_MAIN_ROLE_ORDER = ("opus", "sonnet", "fable", "haiku")
_CODEX_CUSTOM_EFFORTS = ("low", "medium", "high", "xhigh")
_STABLE_ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodexHomeDeletion:
    """记录 Codex 连接家移入墓碑后的补偿信息。

    Attributes:
        home: 数据库仍处于活动状态时应恢复到的托管家路径。
        tombstone: 数据库确认软删除后才能永久清理的临时路径。
    """

    home: Path
    tombstone: Path


@dataclass(frozen=True)
class ConnectionStorageDeletion:
    """记录软删除前暂存的 Codex 与 Claude 连接家。

    Attributes:
        codex_home: 需要在数据库提交后永久清理的 Codex 连接家。
        claude_home: 需要长期保留到后续清理 slice 的 Claude 连接家墓碑。
    """

    codex_home: CodexHomeDeletion | None = None
    claude_home: ClaudeHomeTombstone | None = None


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


def _proxy_url_with_credentials(
    proxy_url: str | None,
    username: str | None,
    password: str | None,
) -> str | None:
    """只在 runtime 内存中把独立代理认证字段合成标准 URL。"""

    if not proxy_url or not username:
        return proxy_url
    parsed = urlsplit(proxy_url)
    userinfo = quote(username, safe="")
    if password:
        userinfo += f":{quote(password, safe='')}"
    return urlunsplit(
        (parsed.scheme, f"{userinfo}@{parsed.netloc}", parsed.path, "", "")
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


def merge_codex_catalog(
    model_ids: Sequence[str],
    *,
    saved_entries: Sequence[CodexCatalogEntry] = (),
    native_models: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[CodexCatalogEntry, ...]:
    """按 Codex 原生顺序合并上游可见模型与已保存自定义模型。

    原生 ``model/list`` 负责 GPT 交互模型的顺序、默认 effort 和支持集合；只要存在
    原生交集，就排除 image/auto-review 等额外端点条目。非空原生目录完全不认识当前
    上游时按上游顺序回退，保证 DeepSeek 等自定义模型第一次获取后即可选择；原生目录
    缺失或为空时不猜测候选。

    Args:
        model_ids: 当前连接上游模型端点返回的真实 ID。
        saved_entries: 连接已经保存的 Codex 自定义模型元数据。
        native_models: Codex app-server ``model/list`` 的内部标准化结果。

    Returns:
        可供 Codex 交互会话使用的有序模型元数据。
    """

    if not native_models:
        return ()
    upstream = set(model_ids)
    entries: list[CodexCatalogEntry] = []
    seen: set[str] = set()
    for row in native_models or ():
        model_id = row.get("id")
        if not isinstance(model_id, str) or model_id not in upstream:
            continue
        efforts = tuple(
            value
            for item in row.get("supported_efforts", ())
            if isinstance(item, Mapping)
            and isinstance((value := item.get("value")), str)
        )
        default_effort = row.get("default_effort")
        entries.append(
            CodexCatalogEntry(
                id=model_id,
                display_name=None,
                default_effort=(
                    default_effort if isinstance(default_effort, str) else None
                ),
                supported_efforts=efforts,
            )
        )
        seen.add(model_id)
    if not entries:
        saved_by_id = {entry.id: entry for entry in saved_entries}
        entries.extend(
            saved_by_id.get(model_id)
            or CodexCatalogEntry(
                id=model_id,
                default_effort="high",
                supported_efforts=_CODEX_CUSTOM_EFFORTS,
            )
            for model_id in dict.fromkeys(model_ids)
        )
    return tuple(entries)


class ConfigurationService:
    """执行配置领域校验并保持跨表更新的一致性。

    Attributes:
        repository: 当前请求或作业使用的配置仓储。
        catalog_fetcher: 获取第三方模型列表的可替换客户端。
        claude_homes: 管理 Claude 连接家与继承发布的存储边界。
        codex_homes: 管理 Codex 连接家与双来源继承发布的存储边界。
    """

    def __init__(
        self,
        repository: ConfigurationRepository,
        *,
        catalog_fetcher: Any | None = None,
        official_account_root: Path | None = None,
        claude_homes: ClaudeConnectionHomeStore | None = None,
        codex_homes: CodexConnectionHomeStore | None = None,
    ) -> None:
        """保存仓储、模型客户端与两个 runtime 的私有目录边界。"""

        self.repository = repository
        self.catalog_fetcher = catalog_fetcher or HttpModelCatalogFetcher()
        self.codex_homes = codex_homes or CodexConnectionHomeStore(
            root=(
                official_account_root
                if official_account_root is not None
                else resolve_application_data_root() / "codex-accounts"
            )
        )
        self.official_account_root = self.codex_homes.root
        self.claude_homes = claude_homes or ClaudeConnectionHomeStore()

    def restrict_codex_catalog_candidates(
        self,
        connection_id: str,
        *,
        catalog: CatalogView,
        candidates: Sequence[CodexCatalogEntry],
    ) -> CatalogView:
        """把 Codex 上游快照收窄为 runtime 可交互候选。

        设置页仍通过响应取得候选元数据；持久快照只保留最终候选，使旧的不兼容选择
        在刷新后无法继续进入 Agent 或会话配置。
        """

        if (
            not catalog.request_identity
            or not catalog.source_endpoint
            or not catalog.fetched_at
        ):
            raise ConfigurationError("CATALOG_STALE", "Codex 模型列表缺少请求身份")
        snapshot = self.repository.get_model_catalog(catalog.request_identity)
        if snapshot is None or snapshot["connection_id"] != connection_id:
            raise ConfigurationError("CATALOG_STALE", "Codex 模型列表已经过期")
        model_ids = tuple(dict.fromkeys(entry.id for entry in candidates))
        self.repository.put_model_catalog(
            request_identity=catalog.request_identity,
            connection_id=connection_id,
            models=model_ids,
            source_endpoint=catalog.source_endpoint,
            fetched_at=catalog.fetched_at,
        )
        return replace(catalog, models=model_ids)

    def create_connection(self, draft: ConnectionDraft) -> ConnectionView:
        """校验并创建一条缺省为未验证状态的连接。"""

        connection_id = str(uuid.uuid4())
        if draft.kind is ConnectionKind.CODEX_OFFICIAL and draft.login_directory:
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID",
                "Codex Official 账号目录由 Trowel 自动管理",
            )
        slot = self._codex_connection_home_path(connection_id)
        prepared = (
            replace(draft, login_directory=str(slot))
            if draft.kind is ConnectionKind.CODEX_OFFICIAL
            else draft
        )
        normalized = self._validate_draft(prepared)
        if draft.kind is ConnectionKind.CODEX_OFFICIAL:
            slot.mkdir(parents=True, exist_ok=True, mode=0o700)
            slot.chmod(0o700)
        now = _now()
        values = {
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
            "claude_auto_memory_disabled": int(normalized.claude_auto_memory_disabled),
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        try:
            self.repository.insert_connection(values)
        except BaseException:
            if draft.kind is ConnectionKind.CODEX_OFFICIAL and slot.is_dir():
                slot.rmdir()
            raise
        return self.get_connection(connection_id)

    def migrate_official_account_slots(self) -> int:
        """把历史 Official 目录引用改成当前数据根下的独立空账号槽。

        迁移只重写引用并创建私有目录，不复制或删除旧 OAuth 凭据；历史配置因此需要
        各自重新登录，避免多个供应商继续共享 ``~/.codex`` 或正式版账号。
        """

        self.official_account_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.official_account_root.chmod(0o700)
        self._recover_codex_home_tombstones()
        migrated = 0
        for row in self.repository.list_connections():
            if row["kind"] != ConnectionKind.CODEX_OFFICIAL.value:
                continue
            expected = self._codex_connection_home_path(str(row["id"]))
            current = (
                Path(str(row["login_directory"])).expanduser().absolute()
                if row["login_directory"]
                else None
            )
            created_slot = not expected.exists()
            expected.mkdir(parents=True, exist_ok=True, mode=0o700)
            expected.chmod(0o700)
            if current == expected:
                continue
            try:
                self.repository.update_connection(
                    str(row["id"]),
                    expected_version=int(row["version"]),
                    values={
                        "version": int(row["version"]) + 1,
                        "identity_version": int(row["identity_version"]) + 1,
                        "login_directory": str(expected),
                        "validation_status": "stale",
                        "updated_at": _now(),
                    },
                )
            except BaseException:
                if created_slot:
                    expected.rmdir()
                raise
            migrated += 1
        return migrated

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

    def inherit_global_claude_config(
        self,
        connection_id: str,
        *,
        expected_version: int,
    ) -> ConnectionView:
        """把真实全局 Claude 用户配置覆盖发布到一项兼容连接家。

        Args:
            connection_id: 接收配置副本的 Claude 兼容连接 ID。
            expected_version: 用户确认继承时看到的连接乐观版本。

        Returns:
            继承完成后的脱敏连接读模型。

        Raises:
            ConfigurationError: 连接不存在、版本已变化、种类不支持或来源配置损坏。
        """

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        if row["kind"] != ConnectionKind.CLAUDE_COMPATIBLE.value:
            raise ConfigurationError(
                "CLAUDE_HOME_UNSUPPORTED",
                "只有 Claude 兼容供应商可以继承全局 Claude 配置",
                status_code=422,
            )
        try:
            self.claude_homes.inherit_global(
                connection_id,
                forbidden_values=self._known_connection_secrets(),
            )
        except ClaudeConnectionHomeError as exc:
            raise ConfigurationError(
                "CLAUDE_HOME_INHERIT_FAILED", str(exc), status_code=409
            ) from exc
        return self.get_connection(connection_id)

    def inherit_global_codex_config(
        self,
        connection_id: str,
        *,
        expected_version: int,
    ) -> ConnectionView:
        """把两处真实全局 Codex 用户配置覆盖发布到独立连接家。

        Args:
            connection_id: 接收配置副本的 Codex Official 或 Custom 连接 ID。
            expected_version: 用户确认继承时看到的连接乐观版本。

        Returns:
            继承完成后的脱敏连接读模型。

        Raises:
            ConfigurationError: 连接不存在、版本变化、种类不支持或来源不安全。
        """

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        if row["kind"] not in {
            ConnectionKind.CODEX_OFFICIAL.value,
            ConnectionKind.CODEX_CUSTOM.value,
        }:
            raise ConfigurationError(
                "CODEX_HOME_UNSUPPORTED",
                "只有 Codex 连接可以继承全局 Codex 配置",
                status_code=422,
            )
        try:
            self.codex_homes.inherit_global(
                connection_id,
                forbidden_values=self._known_connection_secrets(),
            )
        except CodexConnectionHomeError as exc:
            raise ConfigurationError(
                "CODEX_HOME_INHERIT_FAILED", str(exc), status_code=409
            ) from exc
        return self.get_connection(connection_id)

    def _known_connection_secrets(self) -> tuple[str, ...]:
        """返回当前设置库中的全部已知凭据原值，供配置副本 canary 扫描。"""

        return tuple(
            secret
            for connection in self.repository.list_connections()
            for kind in SecretKind
            if (secret := self.repository.read_secret(str(connection["id"]), kind))
        )

    def list_agent_connection_options(
        self,
    ) -> list[dict[str, Any]]:
        """返回普通 Agent 可选供应商及其已保存模型。

        Claude 只暴露已配置的主会话角色别名；Codex 只暴露设置页已经保存的有序
        catalog。原生 ``model/list`` 只在用户显式刷新候选时读取，组装新会话选项不得
        启动所有 Codex manager。
        """

        options: list[dict[str, Any]] = []
        for connection in self.list_connections():
            if connection.runtime is RuntimeKind.DIRECT_API:
                continue
            if connection.runtime is RuntimeKind.CLAUDE_CODE:
                models = self._claude_agent_models(connection)
                catalog_ready = connection.catalog.status == "ready"
            else:
                compatible_models = set(connection.catalog.models)
                models = [
                    self._codex_agent_model(
                        entry,
                        available=entry.id in compatible_models,
                    )
                    for entry in connection.codex_catalog
                ]
                catalog_ready = connection.catalog.status == "ready"
            auth_ready = connection.auth.status in {"configured", "referenced"}
            if not auth_ready:
                disabled_reason = "auth_missing"
            elif not catalog_ready:
                disabled_reason = "catalog_not_ready"
            elif not models:
                disabled_reason = "model_not_selected"
            elif not any(model["available"] for model in models):
                disabled_reason = "model_selection_stale"
            else:
                disabled_reason = None
            options.append(
                {
                    "id": connection.id,
                    "name": connection.name,
                    "runtime": connection.runtime.value,
                    "kind": connection.kind.value,
                    "identity_version": connection.identity_version,
                    "available": disabled_reason is None,
                    "disabled_reason": disabled_reason,
                    "last_session_choice": connection.last_session_choice,
                    "models": models,
                }
            )
        return options

    @staticmethod
    def _claude_agent_models(connection: ConnectionView) -> list[dict[str, Any]]:
        """把 Claude 角色映射转换成主会话别名，不暴露真实上游 model ID。"""

        available_models = set(connection.catalog.models)
        return [
            {
                "id": role,
                "display_name": role,
                "available": True,
                "disabled_reason": None,
                "efforts": [],
                "default_effort": None,
            }
            for role in _CLAUDE_MAIN_ROLE_ORDER
            if connection.claude_role_models.get(role) in available_models
        ]

    @staticmethod
    def _codex_agent_model(
        entry: CodexCatalogEntry, *, available: bool
    ) -> dict[str, Any]:
        """把一项 Codex 原生目录记录转换成 Agent 选择项。"""

        return {
            "id": entry.id,
            "display_name": None,
            "available": available,
            "disabled_reason": None if available else "model_not_in_catalog",
            "efforts": list(entry.supported_efforts),
            "default_effort": entry.default_effort,
        }

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
        effective_draft = (
            replace(draft, login_directory=str(row["login_directory"]))
            if draft.kind is ConnectionKind.CODEX_OFFICIAL
            and row["kind"] == ConnectionKind.CODEX_OFFICIAL.value
            else draft
        )
        normalized = self._validate_draft(effective_draft)
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
            (
                _json(normalized.claude_role_models)
                if normalized.runtime is RuntimeKind.CLAUDE_CODE
                else None
            ),
        )
        previous_launch_fields = (
            row["runtime"],
            row["kind"],
            row["protocol"],
            row["base_url"],
            row["login_directory"],
            row["proxy_url"],
            row["proxy_username"],
            (
                row["claude_role_models"]
                if row["runtime"] == RuntimeKind.CLAUDE_CODE.value
                else None
            ),
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
                "claude_auto_memory_disabled": int(
                    normalized.claude_auto_memory_disabled
                ),
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
        codex_native_models: Sequence[Mapping[str, Any]] | None = None,
    ) -> CatalogView:
        """获取上游列表，必要时先按 Codex 原生目录过滤，再原子保存最终候选。"""

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        if draft is not None:
            effective_draft = (
                replace(draft, login_directory=str(row["login_directory"]))
                if draft.kind is ConnectionKind.CODEX_OFFICIAL
                else draft
            )
            effective = self._validate_draft(effective_draft)
        else:
            effective = self._draft(row)
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
            await asyncio.to_thread(
                self._record_catalog_fetch_error,
                connection_id,
                expected_version,
                exc,
            )
            raise
        return await asyncio.to_thread(
            self._save_fetched_catalog,
            connection_id,
            expected_version,
            draft,
            codex_native_models,
            effective,
            identity,
            fetched,
        )

    def _record_catalog_fetch_error(
        self,
        connection_id: str,
        expected_version: int,
        exc: ConfigurationError,
    ) -> None:
        """在工作线程中保存仍与当前版本匹配的目录请求失败诊断。"""

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

    def _save_fetched_catalog(
        self,
        connection_id: str,
        expected_version: int,
        draft: ConnectionDraft | None,
        codex_native_models: Sequence[Mapping[str, Any]] | None,
        effective: ConnectionDraft,
        identity: str,
        fetched: FetchedCatalog,
    ) -> CatalogView:
        """在工作线程中复核请求身份，并原子保存一次上游模型目录。"""

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
        model_ids = tuple(
            dict.fromkeys(model.id for model in fetched.models if model.id)
        )
        if codex_native_models is not None:
            model_ids = tuple(
                entry.id
                for entry in merge_codex_catalog(
                    model_ids,
                    saved_entries=effective.codex_catalog,
                    native_models=codex_native_models,
                )
            )
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

    def record_native_codex_catalog(
        self,
        connection_id: str,
        *,
        expected_version: int,
        native_models: Sequence[Mapping[str, Any]],
    ) -> CatalogView:
        """保存 Official app-server 已返回的原生模型 ID 快照。

        Args:
            connection_id: Official 供应商的稳定 ID。
            expected_version: 读取供应商时的乐观版本。
            native_models: 已由 Codex 协议解析器校验的 ``model/list`` 记录。

        Returns:
            写入当前连接后的目录事实。
        """

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if int(row["version"]) != expected_version:
            raise version_conflict()
        draft = self._draft(row)
        if draft.kind is not ConnectionKind.CODEX_OFFICIAL:
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "只有 Codex Official 使用原生账号目录"
            )
        model_ids = tuple(
            dict.fromkeys(
                str(model["id"])
                for model in native_models
                if isinstance(model.get("id"), str) and str(model["id"]).strip()
            )
        )
        if not model_ids:
            raise ConfigurationError("EMPTY_CATALOG", "Codex 返回了空模型列表")
        identity = self._request_identity_for_draft(
            connection_id,
            draft,
            self.repository.secret_versions(connection_id),
        )
        fetched_at = _now()
        source_endpoint = "codex://model/list"
        with self.repository.atomic():
            self.repository.put_model_catalog(
                request_identity=identity,
                connection_id=connection_id,
                models=model_ids,
                source_endpoint=source_endpoint,
                fetched_at=fetched_at,
            )
            self.repository.update_connection(
                connection_id,
                expected_version=expected_version,
                values={
                    "version": expected_version + 1,
                    "catalog_request_identity": identity,
                    "catalog_status": "ready",
                    "catalog_error_code": None,
                    "updated_at": fetched_at,
                },
            )
        return CatalogView(
            status="ready",
            models=model_ids,
            source_endpoint=source_endpoint,
            fetched_at=fetched_at,
            request_identity=identity,
            connection_version=expected_version + 1,
        )

    def delete_connection(
        self, connection_id: str, *, expected_version: int
    ) -> ConnectionStorageDeletion | None:
        """软删除连接与 secret，并暂存 runtime 私有目录等待提交裁决。"""

        row = self.repository.get_connection(connection_id)
        owned_codex_home = self._owned_codex_home(row)
        codex_tombstone: Path | None = None
        if owned_codex_home is not None:
            codex_tombstone = owned_codex_home.with_name(
                f".deleted-{owned_codex_home.name}-{uuid.uuid4().hex}"
            )
            owned_codex_home.rename(codex_tombstone)
        claude_deletion: ClaudeHomeTombstone | None = None
        if row is not None and row["kind"] == ConnectionKind.CLAUDE_COMPATIBLE.value:
            try:
                claude_deletion = self.claude_homes.stage_deletion(connection_id)
            except ClaudeConnectionHomeError as exc:
                if (
                    codex_tombstone is not None
                    and codex_tombstone.exists()
                    and owned_codex_home is not None
                    and not owned_codex_home.exists()
                ):
                    codex_tombstone.rename(owned_codex_home)
                raise ConfigurationError(
                    "CLAUDE_HOME_INVALID", str(exc), status_code=409
                ) from exc
        try:
            with self.repository.atomic():
                self._delete_connection(
                    connection_id, expected_version=expected_version
                )
        except BaseException:
            if (
                codex_tombstone is not None
                and codex_tombstone.exists()
                and owned_codex_home is not None
                and not owned_codex_home.exists()
            ):
                codex_tombstone.rename(owned_codex_home)
            self.claude_homes.restore_deletion(claude_deletion)
            raise
        codex_deletion = (
            CodexHomeDeletion(
                home=owned_codex_home,
                tombstone=codex_tombstone,
            )
            if codex_tombstone is not None and owned_codex_home is not None
            else None
        )
        if codex_deletion is None and claude_deletion is None:
            return None
        return ConnectionStorageDeletion(
            codex_home=codex_deletion,
            claude_home=claude_deletion,
        )

    def finalize_connection_storage_deletion(
        self, deletion: ConnectionStorageDeletion | None
    ) -> None:
        """数据库提交后永久清理 Codex 连接家，保留 Claude 历史家。"""

        codex_home = deletion.codex_home if deletion is not None else None
        if codex_home is None or not codex_home.tombstone.exists():
            return
        try:
            shutil.rmtree(codex_home.tombstone)
        except OSError:
            _log.exception("Codex 连接家延迟清理失败：%s", codex_home.tombstone.name)

    def restore_connection_storage_deletion(
        self, deletion: ConnectionStorageDeletion | None
    ) -> None:
        """数据库提交失败时恢复 Codex 与 Claude 连接家。"""

        codex_home = deletion.codex_home if deletion is not None else None
        if (
            codex_home is not None
            and codex_home.tombstone.exists()
            and not codex_home.home.exists()
        ):
            codex_home.tombstone.rename(codex_home.home)
        self.claude_homes.restore_deletion(
            deletion.claude_home if deletion is not None else None
        )

    def _recover_codex_home_tombstones(self) -> None:
        """按数据库软删除状态恢复 Codex 家墓碑，或清理已提交墓碑。"""

        for tombstone in self.official_account_root.glob(".deleted-*"):
            if not tombstone.is_dir() or tombstone.is_symlink():
                continue
            connection_id = self._connection_id_from_tombstone(tombstone)
            if connection_id is None:
                _log.error("无法识别 Codex 连接家墓碑：%s", tombstone.name)
                continue
            row = self.repository.get_connection(connection_id, include_deleted=True)
            active_codex = (
                row is not None
                and row["deleted_at"] is None
                and row["kind"]
                in {
                    ConnectionKind.CODEX_OFFICIAL.value,
                    ConnectionKind.CODEX_CUSTOM.value,
                }
            )
            if active_codex:
                slot = self._codex_connection_home_path(connection_id)
                if slot.exists():
                    _log.error("Codex 连接家与待恢复墓碑同时存在：%s", tombstone.name)
                    continue
                tombstone.rename(slot)
                continue
            try:
                shutil.rmtree(tombstone)
            except OSError:
                _log.exception("Codex 连接家墓碑仍无法清理：%s", tombstone.name)

    @staticmethod
    def _connection_id_from_tombstone(tombstone: Path) -> str | None:
        """从当前版本墓碑名称解析并规范化连接 UUID。"""

        payload = tombstone.name.removeprefix(".deleted-")
        if len(payload) < 38 or payload[36] != "-":
            return None
        try:
            connection_id = str(uuid.UUID(payload[:36]))
        except ValueError:
            return None
        return connection_id if payload.startswith(f"{connection_id}-") else None

    def _owned_codex_home(self, row: sqlite3.Row | None) -> Path | None:
        """只返回当前 Codex 连接在托管根中的精确配置家。"""

        if row is None or row["kind"] not in {
            ConnectionKind.CODEX_OFFICIAL.value,
            ConnectionKind.CODEX_CUSTOM.value,
        }:
            return None
        try:
            expected = self.codex_homes.existing_home(str(row["id"]))
        except CodexConnectionHomeError as exc:
            raise ConfigurationError(
                "CODEX_HOME_INVALID", str(exc), status_code=409
            ) from exc
        if expected is None:
            return None
        if row["kind"] == ConnectionKind.CODEX_CUSTOM.value:
            return expected
        current = (
            Path(str(row["login_directory"])).expanduser().absolute()
            if row["login_directory"]
            else None
        )
        return expected if current == expected else None

    def _codex_connection_home_path(self, connection_id: str) -> Path:
        """返回不会跟随符号链接的 Codex 托管连接家路径。"""

        try:
            return self.codex_homes.home_for(connection_id)
        except CodexConnectionHomeError as exc:
            raise ConfigurationError("OFFICIAL_ACCOUNT_SLOT_INVALID", str(exc)) from exc

    def resolve_agent_session_defaults(
        self, fallback: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """解析显式 Agent 默认配置；缺失或失效时不采用任何隐式回退。"""

        defaults = self.get_agent_defaults()
        if defaults.session_configuration_id is None:
            return None
        try:
            configuration = self.get_session_configuration(
                defaults.session_configuration_id
            )
        except ConfigurationError:
            return None
        if (
            configuration.availability != "available"
            or configuration.runtime is RuntimeKind.DIRECT_API
        ):
            return None
        resolved: dict[str, Any] = {
            "runtime": configuration.runtime.value,
            "connection_id": configuration.connection_id,
            "model": configuration.model,
            "effort": configuration.effort or "",
        }
        runtime = resolved["runtime"]
        resolved["permission_mode"] = (
            defaults.permission or ""
            if runtime == RuntimeKind.CLAUDE_CODE.value
            else ""
        )
        if runtime == RuntimeKind.CODEX.value:
            if defaults.permission:
                resolved["permission_preset"] = defaults.permission
        else:
            resolved.pop("permission_preset", None)
        resolved["memory_enabled"] = defaults.memory_enabled
        resolved["profile_enabled"] = defaults.profile_enabled
        resolved["self_enabled"] = defaults.self_enabled
        resolved.setdefault("model", "")
        resolved.setdefault("effort", "")
        return resolved

    def _delete_connection(self, connection_id: str, *, expected_version: int) -> None:
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
        alias = self._validate_session_alias(draft)
        self._validate_agent_callable(runtime, draft)
        configuration_id = str(uuid.uuid4())
        now = _now()
        with self.repository.atomic():
            self.repository.insert_session_configuration(
                {
                    "id": configuration_id,
                    "version": 1,
                    "identity_version": 1,
                    "name": draft.name.strip(),
                    "runtime": runtime.value,
                    "connection_id": draft.connection_id,
                    "connection_identity_version": int(row["identity_version"]),
                    "model": model,
                    "effort": draft.effort,
                    "capability_version": capability.version,
                    "stable_alias": alias,
                    "agent_callable": int(draft.agent_callable),
                    "deleted_at": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            if alias is not None:
                self._reserve_session_alias(alias, configuration_id, now)
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
        alias = self._validate_session_alias(draft)
        self._validate_agent_callable(RuntimeKind(connection["runtime"]), draft)
        previous_alias = (
            str(current["stable_alias"])
            if current["stable_alias"] is not None
            else None
        )
        identity_changed = (
            str(current["runtime"]),
            str(current["connection_id"]),
            str(current["model"]),
            str(current["effort"]) if current["effort"] is not None else None,
        ) != (
            str(connection["runtime"]),
            draft.connection_id,
            model,
            draft.effort,
        )
        now = _now()
        with self.repository.atomic():
            if alias != previous_alias:
                if previous_alias is not None:
                    self.repository.retire_session_configuration_alias(
                        alias=previous_alias,
                        configuration_id=configuration_id,
                        retired_at=now,
                    )
                if alias is not None:
                    self._reserve_session_alias(alias, configuration_id, now)
            self.repository.update_session_configuration(
                configuration_id,
                expected_version=expected_version,
                values={
                    "version": expected_version + 1,
                    "identity_version": int(current["identity_version"])
                    + int(identity_changed),
                    "name": draft.name.strip(),
                    "runtime": str(connection["runtime"]),
                    "connection_id": draft.connection_id,
                    "connection_identity_version": int(connection["identity_version"]),
                    "model": model,
                    "effort": draft.effort,
                    "capability_version": capability.version,
                    "stable_alias": alias,
                    "agent_callable": int(draft.agent_callable),
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

    def list_agent_callable_configurations(
        self,
    ) -> tuple[SessionConfigurationView, ...]:
        """返回当前可由父 Agent 通过主别名调用的运行配置。"""

        return tuple(
            item
            for item in self.list_session_configurations()
            if item.agent_callable
            and item.stable_alias is not None
            and item.runtime is not RuntimeKind.DIRECT_API
            and item.availability == "available"
        )

    @staticmethod
    def _validate_session_alias(draft: SessionConfigurationDraft) -> str | None:
        """规范化稳定别名，并校验 Agent 调用所需的显式别名。"""

        alias = (draft.stable_alias or "").strip() or None
        if alias is not None and _STABLE_ALIAS_PATTERN.fullmatch(alias) is None:
            raise ConfigurationError(
                "ALIAS_INVALID",
                "稳定别名必须以小写字母开头，且只能包含小写字母、数字、短横线和下划线",
                status_code=422,
            )
        if draft.agent_callable and alias is None:
            raise ConfigurationError(
                "ALIAS_REQUIRED",
                "允许 Agent 调用时必须填写稳定别名",
                status_code=422,
            )
        return alias

    def _reserve_session_alias(
        self, alias: str, configuration_id: str, created_at: str
    ) -> None:
        """登记或收回本配置别名，并把跨配置冲突映射成领域错误。"""

        try:
            self.repository.put_session_configuration_alias(
                alias=alias,
                configuration_id=configuration_id,
                created_at=created_at,
            )
        except ValueError as exc:
            raise ConfigurationError(
                "ALIAS_RESERVED",
                "这个稳定别名已经被另一个当前或历史运行配置使用",
                status_code=409,
            ) from exc

    @staticmethod
    def _validate_agent_callable(
        runtime: RuntimeKind,
        draft: SessionConfigurationDraft,
    ) -> None:
        """拒绝把没有 Agent 会话语义的 direct API 开放给 MCP Agent。

        Args:
            runtime: 草稿引用连接对应的执行方式。
            draft: 包含 Agent 调用策略的运行配置草稿。
        """

        if runtime is RuntimeKind.DIRECT_API and draft.agent_callable:
            raise ConfigurationError(
                "AGENT_RUNTIME_REQUIRED",
                "Direct API 运行配置只能用于后台任务",
                status_code=422,
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

    def resolve_task_launch(self, task_id: TaskId) -> RuntimeLaunchConfiguration:
        """为一次后台任务读取并冻结当前绑定的启动配置。

        Args:
            task_id: 即将开始执行的后台任务。

        Returns:
            已重新核对连接身份、模型目录和能力资格的秘密启动配置。

        Raises:
            ConfigurationError: 任务未绑定、运行配置已归档或已过期，或该组合不再
                具备任务资格。
        """

        binding = next(
            (item for item in self.list_task_bindings() if item.task_id is task_id),
            None,
        )
        if binding is None or binding.session_configuration_id is None:
            raise ConfigurationError(
                "TASK_CONFIGURATION_REQUIRED",
                "后台任务尚未绑定运行配置",
                status_code=409,
            )
        row = self.repository.get_session_configuration(
            binding.session_configuration_id
        )
        if row is None:
            raise ConfigurationError(
                "TASK_CONFIGURATION_STALE",
                "后台任务绑定的运行配置已归档",
                status_code=409,
            )
        configuration = self._session_view(row)
        if configuration.availability != "available":
            raise ConfigurationError(
                "TASK_CONFIGURATION_STALE",
                "后台任务绑定的运行配置已经过期",
                status_code=409,
            )
        if task_id not in configuration.capability.eligible_tasks:
            raise ConfigurationError(
                "TASK_UNSUPPORTED",
                "后台任务绑定的运行配置不再具备这项任务资格",
                status_code=409,
            )
        return self.resolve_runtime_launch(
            configuration.connection_id,
            model=configuration.model,
            effort=configuration.effort,
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

    def resolve_runtime_launch(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> RuntimeLaunchConfiguration:
        """把已验证连接解析成只在后端内存中存在的冻结启动配置。

        Args:
            connection_id: Agent 表单选择的稳定连接 ID。
            model: Codex 真实模型 ID，或 Claude Code 的主会话角色别名。
            effort: 本次会话选择的思考强度；Claude 可为 None。

        Returns:
            同时包含脱敏身份和秘密启动材料的内部值对象。

        Raises:
            ConfigurationError: 连接、catalog、认证或模型不可用。
        """

        connection = self.repository.get_connection(connection_id)
        expected_version = int(connection["version"]) if connection is not None else -1
        row, selected_model, capability = self._validate_session_draft(
            SessionConfigurationDraft(
                name="Agent runtime launch",
                connection_id=connection_id,
                model=model,
                effort=effort,
            ),
            expected_connection_version=expected_version,
        )
        return self._build_runtime_launch(
            row,
            draft=self._draft(row),
            model=selected_model,
            effort=effort,
            capability_version=capability.version,
            capability_source=capability.source,
        )

    def resolve_codex_catalog_launch(
        self,
        connection_id: str,
        *,
        draft: ConnectionDraft | None = None,
        model_ids: Sequence[str] = (),
    ) -> RuntimeLaunchConfiguration:
        """为某一条 Codex 连接构造只用于读取原生模型目录的启动配置。

        目录发现不依赖已保存的模型可用性，否则 Codex official 无法在第一次
        打开时发现模型。传入未保存草稿时，返回值冻结该草稿的 provider 和代理
        边界，不会污染其他连接的 app-server。

        Args:
            connection_id: 提供凭据和身份版本的已保存连接 ID。
            draft: 设置页当前未保存的非 secret 字段；为 None 时使用已保存值。
            model_ids: 本次上游列表候选，仅用于填充内部占位模型字段。

        Returns:
            只供 Codex manager pool 启动连接专属 app-server 的内部值对象。

        Raises:
            ConfigurationError: 连接不存在、不是 Codex，或认证材料不可用。
        """

        row = self.repository.get_connection(connection_id)
        if row is None:
            raise not_found("连接")
        if draft is None:
            effective = self._draft(row)
        else:
            effective_draft = (
                replace(draft, login_directory=str(row["login_directory"]))
                if draft.kind is ConnectionKind.CODEX_OFFICIAL
                and row["kind"] == ConnectionKind.CODEX_OFFICIAL.value
                else draft
            )
            effective = self._validate_draft(effective_draft)
        if effective.runtime is not RuntimeKind.CODEX:
            raise ConfigurationError(
                "AGENT_RUNTIME_REQUIRED",
                "模型目录只能由 Codex 连接读取",
                status_code=422,
            )
        model = next(
            (
                candidate.strip()
                for candidate in model_ids
                if isinstance(candidate, str) and candidate.strip()
            ),
            None,
        )
        if model is None and effective.codex_catalog:
            model = effective.codex_catalog[0].id
        return self._build_runtime_launch(
            row,
            draft=effective,
            model=model or "codex-catalog-discovery",
            effort=None,
            capability_version=CAPABILITY_REGISTRY_VERSION,
            capability_source="Codex native model/list discovery",
        )

    def _build_runtime_launch(
        self,
        row: sqlite3.Row,
        *,
        draft: ConnectionDraft,
        model: str,
        effort: str | None,
        capability_version: str,
        capability_source: str | None,
    ) -> RuntimeLaunchConfiguration:
        """用已保存凭据和指定非 secret 配置构造冻结的 runtime 启动对象。

        Args:
            row: 提供连接 ID、版本和 secret 查询键的持久化行。
            draft: 本次启动应使用的已校验非 secret 字段。
            model: 会话模型，或仅目录发现时的内部占位值。
            effort: 会话思考强度；目录发现时为 None。
            capability_version: 放行本次启动的能力表版本。
            capability_source: 放行本次启动的证据说明。

        Returns:
            不会被 API 序列化的 runtime 内部启动配置。

        Raises:
            ConfigurationError: 应用凭据或 Codex official 登录目录不可用。
        """

        connection_id = str(row["id"])
        api_key = self.repository.read_secret(connection_id, SecretKind.API_KEY)
        if draft.kind is not ConnectionKind.CODEX_OFFICIAL and not api_key:
            raise ConfigurationError(
                "AUTH_MISSING", "连接凭据尚未配置", status_code=422
            )
        if draft.kind is ConnectionKind.CODEX_OFFICIAL:
            expected_slot = self._codex_connection_home_path(connection_id)
            configured_slot = (
                Path(draft.login_directory).expanduser().absolute()
                if draft.login_directory
                else None
            )
            if configured_slot != expected_slot or not expected_slot.is_dir():
                raise ConfigurationError(
                    "LOGIN_DIRECTORY_MISSING",
                    "Codex Official 账号槽尚未完成安全迁移",
                    status_code=422,
                )
            api_key = None
        proxy_url = _proxy_url_with_credentials(
            draft.proxy_url,
            draft.proxy_username,
            self.repository.read_secret(connection_id, SecretKind.PROXY_PASSWORD),
        )
        claude_config_dir: str | None = None
        claude_plugin_dir: str | None = None
        codex_config_dir: str | None = None
        if draft.kind is ConnectionKind.CLAUDE_COMPATIBLE:
            try:
                claude_config_dir = str(self.claude_homes.ensure_home(connection_id))
                claude_plugin_dir = str(self.claude_homes.shared_plugin_root)
            except ClaudeConnectionHomeError as exc:
                raise ConfigurationError(
                    "CLAUDE_HOME_INVALID", str(exc), status_code=409
                ) from exc
        if draft.runtime is RuntimeKind.CODEX:
            try:
                codex_config_dir = str(self.codex_homes.ensure_home(connection_id))
            except CodexConnectionHomeError as exc:
                raise ConfigurationError(
                    "CODEX_HOME_INVALID", str(exc), status_code=409
                ) from exc
        return RuntimeLaunchConfiguration(
            connection_id=connection_id,
            connection_version=int(row["version"]),
            connection_identity_version=int(row["identity_version"]),
            connection_name=draft.name,
            runtime=draft.runtime,
            kind=draft.kind,
            protocol=draft.protocol,
            model=model,
            effort=effort,
            capability_version=capability_version,
            base_url=draft.base_url,
            login_directory=draft.login_directory,
            proxy_url=proxy_url,
            claude_role_models=dict(draft.claude_role_models),
            codex_catalog=draft.codex_catalog,
            claude_config_dir=claude_config_dir,
            claude_plugin_dir=claude_plugin_dir,
            codex_config_dir=codex_config_dir,
            api_key=api_key,
            capability_source=capability_source,
            claude_auto_memory_disabled=draft.claude_auto_memory_disabled,
        )

    def _validate_session_draft(
        self,
        draft: SessionConfigurationDraft,
        *,
        expected_connection_version: int,
    ) -> tuple[sqlite3.Row, str, CapabilityView]:
        """返回通过当前 catalog 的连接、交互模型与后台任务能力。"""

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
        runtime = RuntimeKind(row["runtime"])
        catalog_model = model
        if runtime is RuntimeKind.CLAUDE_CODE:
            role_models = _load_json(row["claude_role_models"], {})
            if isinstance(role_models, dict) and isinstance(
                role_models.get(model), str
            ):
                catalog_model = str(role_models[model])
        if catalog_model not in catalog.models:
            raise ConfigurationError(
                "MODEL_NOT_IN_CATALOG", "所选模型不在当前模型列表中", status_code=422
            )
        capability = capability_for(
            runtime,
            ConnectionKind(row["kind"]),
            ProtocolKind(row["protocol"]),
            catalog_model,
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
            raise ConfigurationError("NAME_REQUIRED", "供应商名称不能为空")
        if len(name) > 120:
            raise ConfigurationError("NAME_INVALID", "供应商名称过长")
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
            if (
                base_url is not None
                or models_url is not None
                or login_directory is None
            ):
                raise ConfigurationError(
                    "CONNECTION_SHAPE_INVALID",
                    "Codex Official 不接受模型服务地址，账号槽由 Trowel 管理",
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
        if draft.kind != ConnectionKind.CLAUDE_COMPATIBLE and draft.claude_role_models:
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "只有 Claude Code 连接可以保存角色映射"
            )
        if (
            draft.kind
            not in {
                ConnectionKind.CODEX_CUSTOM,
                ConnectionKind.CODEX_OFFICIAL,
            }
            and draft.codex_catalog
        ):
            raise ConfigurationError(
                "CONNECTION_SHAPE_INVALID", "只有 Codex 供应商可以保存模型目录"
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
            claude_auto_memory_disabled=bool(row["claude_auto_memory_disabled"]),
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
        login_directory = row["login_directory"]
        auth_status = (
            (
                "referenced"
                if login_directory
                and (Path(str(login_directory)).expanduser() / "auth.json").is_file()
                else "missing"
            )
            if row["auth_kind"] == "oauth_reference"
            else (
                "configured"
                if SecretKind.API_KEY.value in configured_secrets
                else "missing"
            )
        )
        base_url = row["base_url"]
        upstream_host = urlsplit(base_url).hostname if base_url else None
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
            login_directory=None,
            login_directory_exists=(
                Path(login_directory).expanduser().exists() if login_directory else None
            ),
            claude_config_inherited=(
                self.claude_homes.is_inherited(connection_id)
                if row["kind"] == ConnectionKind.CLAUDE_COMPATIBLE.value
                else None
            ),
            codex_config_inherited=(
                self.codex_homes.is_inherited(connection_id)
                if row["kind"]
                in {
                    ConnectionKind.CODEX_OFFICIAL.value,
                    ConnectionKind.CODEX_CUSTOM.value,
                }
                else None
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
            claude_auto_memory_disabled=bool(row["claude_auto_memory_disabled"]),
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
                "trowel_connection_home": {
                    "inherited": self.claude_homes.is_inherited(str(row["id"])),
                    "provider_settings": "per-session override",
                },
            }
            if bool(row["claude_auto_memory_disabled"]):
                body["autoMemoryEnabled"] = False
            return {"format": "json", "body": body}
        if row["kind"] == ConnectionKind.CODEX_OFFICIAL.value:
            return {
                "format": "toml",
                "body": {
                    "provider": "openai",
                    "account": f"<{auth_status}>",
                    "oauth": "<Codex managed>",
                    "trowel_connection_home": {
                        "inherited": self.codex_homes.is_inherited(str(row["id"])),
                        "provider_settings": "manager override",
                    },
                },
            }
        body = {
            "model_provider": row["id"],
            "base_url": row["base_url"],
            "wire_api": row["protocol"],
            "api_key": f"<{auth_status}>",
            "models": _load_json(row["codex_catalog"], []),
        }
        if row["kind"] == ConnectionKind.CODEX_CUSTOM.value:
            body["trowel_connection_home"] = {
                "inherited": self.codex_homes.is_inherited(str(row["id"])),
                "provider_settings": "manager override",
            }
        return {"format": "toml", "body": body}

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
            stored_model = str(row["model"])
            catalog_model = stored_model
            if runtime is RuntimeKind.CLAUDE_CODE:
                role_models = _load_json(connection["claude_role_models"], {})
                if isinstance(role_models, dict) and isinstance(
                    role_models.get(stored_model), str
                ):
                    catalog_model = str(role_models[stored_model])
            capability = capability_for(
                runtime,
                ConnectionKind(connection["kind"]),
                ProtocolKind(connection["protocol"]),
                catalog_model,
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
            elif catalog_model not in catalog.models:
                availability, reason = "stale", "model_not_in_catalog"
            else:
                availability, reason = "available", None
        return SessionConfigurationView(
            id=str(row["id"]),
            version=int(row["version"]),
            identity_version=int(row["identity_version"]),
            name=str(row["name"]),
            runtime=runtime,
            connection_id=str(row["connection_id"]),
            connection_identity_version=int(row["connection_identity_version"]),
            model=str(row["model"]),
            effort=str(row["effort"]) if row["effort"] is not None else None,
            capability=capability,
            availability=availability,
            disabled_reason=reason,
            connection_name=(
                str(connection["name"]) if connection is not None else "连接已删除"
            ),
            stable_alias=(
                str(row["stable_alias"]) if row["stable_alias"] is not None else None
            ),
            agent_callable=bool(row["agent_callable"]),
        )
