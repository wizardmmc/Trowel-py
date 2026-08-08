"""在主 SQLite 中原子保存配置事实和只写 secret。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from trowel_py.configuration.errors import not_found, version_conflict
from trowel_py.configuration.models import SecretKind

_CONNECTION_COLUMNS = frozenset(
    {
        "version",
        "identity_version",
        "name",
        "runtime",
        "kind",
        "protocol",
        "base_url",
        "models_url",
        "auth_kind",
        "login_directory",
        "proxy_url",
        "proxy_username",
        "claude_role_models",
        "codex_catalog",
        "catalog_request_identity",
        "catalog_status",
        "catalog_error_code",
        "validation_status",
        "capability_version",
        "last_session_choice",
        "claude_auto_memory_disabled",
        "deleted_at",
        "updated_at",
    }
)

_SESSION_CONFIGURATION_COLUMNS = frozenset(
    {
        "version",
        "identity_version",
        "name",
        "runtime",
        "connection_id",
        "connection_identity_version",
        "model",
        "effort",
        "capability_version",
        "stable_alias",
        "agent_callable",
        "deleted_at",
        "updated_at",
    }
)


class ConfigurationRepository:
    """提供配置领域的事务内 SQLite 读写操作。

    Attributes:
        connection: 调用方持有生命周期和提交边界的主数据库连接。
        _savepoint_sequence: 为嵌套领域操作分配连接内唯一保存点名称。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """保存已启用字典行和外键的主数据库连接。"""

        self.connection = connection
        self._savepoint_sequence = 0
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """用外层事务和保存点保证领域写入可由请求统一提交或回滚。"""

        self._savepoint_sequence += 1
        savepoint = f"configuration_operation_{self._savepoint_sequence}"
        started_transaction = not self.connection.in_transaction
        if started_transaction:
            self.connection.execute("BEGIN")
        self.connection.execute(f"SAVEPOINT {savepoint}")  # noqa: S608 - 名称只含内部计数。
        try:
            yield
        except BaseException:
            self.connection.execute(f"ROLLBACK TO {savepoint}")  # noqa: S608 - 名称只含内部计数。
            self.connection.execute(f"RELEASE {savepoint}")  # noqa: S608 - 名称只含内部计数。
            if started_transaction:
                self.connection.rollback()
            raise
        else:
            self.connection.execute(f"RELEASE {savepoint}")  # noqa: S608 - 名称只含内部计数。

    def insert_connection(self, values: Mapping[str, Any]) -> None:
        """插入一条已由 service 完成字段校验的连接。"""

        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.execute(
            f"INSERT INTO configuration_connections ({', '.join(columns)}) "  # noqa: S608 - 列名来自 service 固定字典。
            f"VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )

    def get_connection(
        self,
        connection_id: str,
        *,
        include_deleted: bool = False,
    ) -> sqlite3.Row | None:
        """按稳定 ID 读取连接，可选择保留软删除历史。"""

        suffix = "" if include_deleted else " AND deleted_at IS NULL"
        return self.connection.execute(
            "SELECT * FROM configuration_connections WHERE id = ?" + suffix,
            (connection_id,),
        ).fetchone()

    def list_connections(
        self, *, include_deleted: bool = False
    ) -> tuple[sqlite3.Row, ...]:
        """按 runtime、名称和 ID 返回连接，默认排除软删除项。"""

        condition = "" if include_deleted else "WHERE deleted_at IS NULL"
        rows = self.connection.execute(
            "SELECT * FROM configuration_connections "
            f"{condition} ORDER BY runtime, name COLLATE NOCASE, id"
        ).fetchall()
        return tuple(rows)

    def update_connection(
        self,
        connection_id: str,
        *,
        expected_version: int,
        values: Mapping[str, Any],
    ) -> None:
        """用乐观版本更新连接；不匹配时区分不存在和版本冲突。"""

        unknown = set(values) - _CONNECTION_COLUMNS
        if unknown:
            raise ValueError(f"unsupported connection columns: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        cursor = self.connection.execute(
            f"UPDATE configuration_connections SET {assignments} "  # noqa: S608 - 列名经过固定白名单校验。
            "WHERE id = ? AND version = ? AND deleted_at IS NULL",
            (*values.values(), connection_id, expected_version),
        )
        if cursor.rowcount == 1:
            return
        if self.get_connection(connection_id, include_deleted=True) is None:
            raise not_found("连接")
        raise version_conflict()

    def put_secret(
        self,
        connection_id: str,
        kind: SecretKind,
        value: str,
        updated_at: str,
    ) -> int:
        """写入或替换 secret，并返回本次落库后的单调版本。"""

        current = self.connection.execute(
            "SELECT version FROM configuration_secret_versions "
            "WHERE connection_id = ? AND kind = ?",
            (connection_id, kind.value),
        ).fetchone()
        version = int(current["version"]) + 1 if current is not None else 1
        self.connection.execute(
            "INSERT INTO configuration_secrets "
            "(connection_id, kind, value, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(connection_id, kind) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at",
            (connection_id, kind.value, value, updated_at),
        )
        self.connection.execute(
            "INSERT INTO configuration_secret_versions "
            "(connection_id, kind, version, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(connection_id, kind) DO UPDATE SET "
            "version = excluded.version, updated_at = excluded.updated_at",
            (connection_id, kind.value, version, updated_at),
        )
        return version

    def delete_secret(
        self,
        connection_id: str,
        kind: SecretKind,
        updated_at: str,
    ) -> int:
        """删除一种 secret，保留并推进不含原值的单调版本。"""

        current = self.connection.execute(
            "SELECT version FROM configuration_secret_versions "
            "WHERE connection_id = ? AND kind = ?",
            (connection_id, kind.value),
        ).fetchone()
        version = int(current["version"]) + 1 if current is not None else 1
        self.connection.execute(
            "DELETE FROM configuration_secrets WHERE connection_id = ? AND kind = ?",
            (connection_id, kind.value),
        )
        self.connection.execute(
            "INSERT INTO configuration_secret_versions "
            "(connection_id, kind, version, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(connection_id, kind) DO UPDATE SET "
            "version = excluded.version, updated_at = excluded.updated_at",
            (connection_id, kind.value, version, updated_at),
        )
        return version

    def delete_all_secrets(self, connection_id: str, updated_at: str) -> None:
        """删除软删除连接的全部 secret，并推进各自版本。"""

        rows = self.connection.execute(
            "SELECT kind FROM configuration_secrets WHERE connection_id = ?",
            (connection_id,),
        ).fetchall()
        for row in rows:
            self.delete_secret(connection_id, SecretKind(row["kind"]), updated_at)

    def read_secret(self, connection_id: str, kind: SecretKind) -> str | None:
        """仅供 runtime 和模型列表客户端读取 secret 原值。"""

        row = self.connection.execute(
            "SELECT value FROM configuration_secrets WHERE connection_id = ? AND kind = ?",
            (connection_id, kind.value),
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def secret_versions(self, connection_id: str) -> dict[str, int]:
        """返回连接的 secret 名称和版本，不读取原值。"""

        rows = self.connection.execute(
            "SELECT kind, version FROM configuration_secret_versions "
            "WHERE connection_id = ?",
            (connection_id,),
        ).fetchall()
        return {str(row["kind"]): int(row["version"]) for row in rows}

    def configured_secret_kinds(self, connection_id: str) -> frozenset[str]:
        """返回当前仍保存原值的 secret 用途，不读取原值。"""

        rows = self.connection.execute(
            "SELECT kind FROM configuration_secrets WHERE connection_id = ?",
            (connection_id,),
        ).fetchall()
        return frozenset(str(row["kind"]) for row in rows)

    def count_secrets(self) -> int:
        """返回 secret 行数，供迁移原子性测试使用。"""

        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM configuration_secrets"
        ).fetchone()
        return int(row["count"])

    def put_model_catalog(
        self,
        *,
        request_identity: str,
        connection_id: str,
        models: tuple[str, ...],
        source_endpoint: str,
        fetched_at: str,
    ) -> None:
        """按不可逆请求身份保存去重后的上游模型列表。"""

        self.connection.execute(
            "INSERT INTO configuration_model_catalogs "
            "(request_identity, connection_id, models, source_endpoint, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(request_identity) DO UPDATE SET "
            "models = excluded.models, source_endpoint = excluded.source_endpoint, "
            "fetched_at = excluded.fetched_at",
            (
                request_identity,
                connection_id,
                json.dumps(models, ensure_ascii=False),
                source_endpoint,
                fetched_at,
            ),
        )

    def get_model_catalog(self, request_identity: str | None) -> sqlite3.Row | None:
        """读取指定请求身份的模型列表快照。"""

        if request_identity is None:
            return None
        return self.connection.execute(
            "SELECT * FROM configuration_model_catalogs WHERE request_identity = ?",
            (request_identity,),
        ).fetchone()

    def insert_session_configuration(self, values: Mapping[str, Any]) -> None:
        """插入一项已经通过 catalog 和 capability 校验的会话配置。"""

        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.execute(
            f"INSERT INTO configuration_session_configs ({', '.join(columns)}) "  # noqa: S608 - 列名来自 service 固定字典。
            f"VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )

    def get_session_configuration(
        self,
        configuration_id: str,
        *,
        include_deleted: bool = False,
    ) -> sqlite3.Row | None:
        """按稳定 ID 读取会话配置。"""

        suffix = "" if include_deleted else " AND deleted_at IS NULL"
        return self.connection.execute(
            "SELECT * FROM configuration_session_configs WHERE id = ?" + suffix,
            (configuration_id,),
        ).fetchone()

    def list_session_configurations(self) -> tuple[sqlite3.Row, ...]:
        """返回所有未软删除的会话配置。"""

        rows = self.connection.execute(
            "SELECT * FROM configuration_session_configs "
            "WHERE deleted_at IS NULL ORDER BY runtime, name COLLATE NOCASE, id"
        ).fetchall()
        return tuple(rows)

    def get_session_configuration_alias(self, alias: str) -> sqlite3.Row | None:
        """读取一个当前或已退役别名的永久归属。"""

        return self.connection.execute(
            "SELECT * FROM configuration_session_aliases WHERE alias = ?",
            (alias,),
        ).fetchone()

    def put_session_configuration_alias(
        self,
        *,
        alias: str,
        configuration_id: str,
        created_at: str,
    ) -> None:
        """登记当前别名；同一配置可收回旧别名，跨配置仍永久隔离。"""

        existing = self.get_session_configuration_alias(alias)
        if existing is not None:
            if str(existing["session_configuration_id"]) != configuration_id:
                raise ValueError("session configuration alias is reserved")
            try:
                self.connection.execute(
                    "UPDATE configuration_session_aliases "
                    "SET is_current = 1, retired_at = NULL "
                    "WHERE alias = ? AND session_configuration_id = ?",
                    (alias, configuration_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("session configuration already has a current alias") from exc
            return

        try:
            self.connection.execute(
                "INSERT INTO configuration_session_aliases "
                "(alias, session_configuration_id, is_current, created_at, retired_at) "
                "VALUES (?, ?, 1, ?, NULL)",
                (alias, configuration_id, created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("session configuration alias is reserved") from exc

    def retire_session_configuration_alias(
        self,
        *,
        alias: str,
        configuration_id: str,
        retired_at: str,
    ) -> None:
        """把配置当前别名转成永久保留但不再公开的兼容别名。"""

        cursor = self.connection.execute(
            "UPDATE configuration_session_aliases "
            "SET is_current = 0, retired_at = ? "
            "WHERE alias = ? AND session_configuration_id = ? AND is_current = 1",
            (retired_at, alias, configuration_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("current session configuration alias is missing")

    def update_session_configuration(
        self,
        configuration_id: str,
        *,
        expected_version: int,
        values: Mapping[str, Any],
    ) -> None:
        """乐观更新或软删除会话配置。"""

        unknown = set(values) - _SESSION_CONFIGURATION_COLUMNS
        if unknown:
            raise ValueError(
                f"unsupported session configuration columns: {sorted(unknown)}"
            )
        assignments = ", ".join(f"{column} = ?" for column in values)
        cursor = self.connection.execute(
            f"UPDATE configuration_session_configs SET {assignments} "  # noqa: S608 - 列名经过固定白名单校验。
            "WHERE id = ? AND version = ? AND deleted_at IS NULL",
            (*values.values(), configuration_id, expected_version),
        )
        if cursor.rowcount == 1:
            return
        if (
            self.get_session_configuration(configuration_id, include_deleted=True)
            is None
        ):
            raise not_found("会话配置")
        raise version_conflict()

    def put_task_binding(
        self,
        *,
        task_id: str,
        session_configuration_id: str,
        expected_version: int,
        updated_at: str,
    ) -> int:
        """创建或乐观更新任务绑定，并返回新版本。"""

        if expected_version == 0:
            try:
                self.connection.execute(
                    "INSERT INTO configuration_task_bindings "
                    "(task_id, version, session_configuration_id, updated_at) "
                    "VALUES (?, 1, ?, ?)",
                    (task_id, session_configuration_id, updated_at),
                )
            except sqlite3.IntegrityError as exc:
                raise version_conflict() from exc
            return 1
        new_version = expected_version + 1
        cursor = self.connection.execute(
            "UPDATE configuration_task_bindings SET version = ?, "
            "session_configuration_id = ?, updated_at = ? "
            "WHERE task_id = ? AND version = ?",
            (
                new_version,
                session_configuration_id,
                updated_at,
                task_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise version_conflict()
        return new_version

    def list_task_bindings(self) -> tuple[sqlite3.Row, ...]:
        """按稳定任务 ID 返回全部后台任务绑定。"""

        return tuple(
            self.connection.execute(
                "SELECT * FROM configuration_task_bindings ORDER BY task_id"
            ).fetchall()
        )

    def delete_task_binding(
        self,
        task_id: str,
        *,
        expected_version: int,
        updated_at: str,
    ) -> int:
        """按乐观版本解除任务绑定并保留单调版本，返回新版本。"""

        version = expected_version + 1
        cursor = self.connection.execute(
            "UPDATE configuration_task_bindings SET version = ?, "
            "session_configuration_id = NULL, updated_at = ? "
            "WHERE task_id = ? AND version = ?",
            (version, updated_at, task_id, expected_version),
        )
        if cursor.rowcount == 1:
            return version
        current = self.connection.execute(
            "SELECT 1 FROM configuration_task_bindings WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if current is None:
            raise not_found("任务绑定")
        raise version_conflict()

    def get_agent_defaults(self) -> sqlite3.Row | None:
        """读取唯一一份新建 Agent 默认条件。"""

        return self.connection.execute(
            "SELECT * FROM configuration_agent_defaults WHERE id = 'default'"
        ).fetchone()

    def put_agent_defaults(
        self,
        *,
        expected_version: int,
        session_configuration_id: str | None,
        permission: str | None,
        memory_enabled: bool,
        profile_enabled: bool,
        self_enabled: bool,
        updated_at: str,
    ) -> int:
        """创建或乐观更新 Agent 默认条件，并返回新版本。"""

        values = (
            session_configuration_id,
            permission,
            int(memory_enabled),
            int(profile_enabled),
            int(self_enabled),
            updated_at,
        )
        if expected_version == 0:
            try:
                self.connection.execute(
                    "INSERT INTO configuration_agent_defaults "
                    "(id, version, session_configuration_id, permission, "
                    "memory_enabled, profile_enabled, self_enabled, updated_at) "
                    "VALUES ('default', 1, ?, ?, ?, ?, ?, ?)",
                    values,
                )
            except sqlite3.IntegrityError as exc:
                raise version_conflict() from exc
            return 1
        version = expected_version + 1
        cursor = self.connection.execute(
            "UPDATE configuration_agent_defaults SET version = ?, "
            "session_configuration_id = ?, permission = ?, memory_enabled = ?, "
            "profile_enabled = ?, self_enabled = ?, updated_at = ? "
            "WHERE id = 'default' AND version = ?",
            (version, *values, expected_version),
        )
        if cursor.rowcount != 1:
            raise version_conflict()
        return version

    def delete_agent_defaults(self, *, expected_version: int, updated_at: str) -> int:
        """按乐观版本重置 Agent 默认条件并保留单调版本。"""

        version = expected_version + 1
        cursor = self.connection.execute(
            "UPDATE configuration_agent_defaults SET version = ?, "
            "session_configuration_id = NULL, permission = NULL, "
            "memory_enabled = 1, profile_enabled = 1, self_enabled = 1, "
            "updated_at = ? WHERE id = 'default' AND version = ?",
            (version, updated_at, expected_version),
        )
        if cursor.rowcount == 1:
            return version
        if self.get_agent_defaults() is None:
            raise not_found("Agent 默认条件")
        raise version_conflict()

    def get_meta(self, key: str) -> str | None:
        """读取配置领域内部迁移或版本标记。"""

        row = self.connection.execute(
            "SELECT value FROM configuration_meta WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def put_meta(self, key: str, value: str, updated_at: str) -> None:
        """写入配置领域内部迁移或版本标记。"""

        self.connection.execute(
            "INSERT INTO configuration_meta (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, updated_at),
        )
