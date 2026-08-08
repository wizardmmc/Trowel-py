"""持久保存原生会话的脱敏冻结条件，供 binding 删除后继续恢复。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from trowel_py.agent_host.binding import DelegationTarget, Runtime, SessionBinding


class ConfigurationArchiveCorruptError(RuntimeError):
    """表示冻结配置档案整体损坏，继续写入会覆盖仍可抢救的历史。"""


@dataclass(frozen=True)
class FrozenSessionConfiguration:
    """保存恢复原生会话所需且不含凭据的创建条件。

    Attributes:
        claude_config_dir: Claude Code 会话创建时冻结的用户配置目录。
            旧档案缺失时为 None，表示继续使用 ``~/.claude``。
        codex_config_dir: Codex 会话创建时冻结的连接配置家。旧档案缺失时
            为 None，表示继续使用修复前的 Official 槽或 Custom 共享根。
        claude_auto_memory_disabled: Claude 会话创建时是否关闭原生 auto-memory；
            旧档案缺失时保持原生默认。
    """

    runtime: Runtime
    native_session_id: str
    connection_id: str | None
    connection_identity_version: int | None
    model: str | None
    effort: str | None
    permission: str | None
    permission_preset: str | None
    memory_enabled: bool
    memory_mcp_enabled: bool
    profile_enabled: bool
    self_enabled: bool
    agent_mcp_enabled: bool
    claude_auto_memory_disabled: bool = False
    claude_config_dir: str | None = None
    codex_config_dir: str | None = None
    delegation_targets: tuple[DelegationTarget, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """转换为稳定 JSON 基本类型。"""

        return {
            "runtime": self.runtime.value,
            "native_session_id": self.native_session_id,
            "connection_id": self.connection_id,
            "connection_identity_version": self.connection_identity_version,
            "model": self.model,
            "effort": self.effort,
            "permission": self.permission,
            "permission_preset": self.permission_preset,
            "memory_enabled": self.memory_enabled,
            "memory_mcp_enabled": self.memory_mcp_enabled,
            "profile_enabled": self.profile_enabled,
            "self_enabled": self.self_enabled,
            "agent_mcp_enabled": self.agent_mcp_enabled,
            "claude_auto_memory_disabled": self.claude_auto_memory_disabled,
            "claude_config_dir": self.claude_config_dir,
            "codex_config_dir": self.codex_config_dir,
            "delegation_targets": [item.to_dict() for item in self.delegation_targets],
        }


class SessionConfigurationArchive:
    """按 runtime 和原生会话 ID 原子读写脱敏冻结条件。"""

    def __init__(self, path: Path) -> None:
        """保存档案路径；文件不存在时按空档案处理。"""

        self.path = path
        self._lock = threading.Lock()

    def put(
        self,
        binding: SessionBinding,
        *,
        claude_auto_memory_disabled: bool = False,
        claude_config_dir: str | Path | None = None,
        codex_config_dir: str | Path | None = None,
    ) -> None:
        """在 binding 已取得原生 ID 后保存或覆盖冻结条件。

        Args:
            binding: 已取得原生会话 ID 的会话绑定。
            claude_auto_memory_disabled: 本会话冻结的 Claude 原生记忆条件。
            claude_config_dir: Claude Code 的连接级配置目录。None
                也是有意义的冻结值，代表全局 ``~/.claude``。
            codex_config_dir: Codex 的连接级配置目录。None 代表沿用旧会话
                在修复前使用的启动目录规则。
        """

        native_session_id = binding.native_session_id
        if not native_session_id:
            return
        record = FrozenSessionConfiguration(
            runtime=binding.runtime,
            native_session_id=native_session_id,
            connection_id=binding.connection_id,
            connection_identity_version=binding.connection_identity_version,
            model=binding.requested_model,
            effort=binding.requested_effort,
            permission=binding.permission,
            permission_preset=binding.permission_preset,
            memory_enabled=binding.memory_enabled,
            memory_mcp_enabled=binding.memory_mcp_enabled,
            profile_enabled=binding.profile_enabled,
            self_enabled=binding.self_enabled,
            agent_mcp_enabled=binding.agent_mcp_enabled,
            claude_auto_memory_disabled=claude_auto_memory_disabled,
            delegation_targets=binding.delegation_targets,
            claude_config_dir=(
                str(Path(claude_config_dir).expanduser().resolve())
                if claude_config_dir is not None
                else None
            ),
            codex_config_dir=(
                str(Path(codex_config_dir).expanduser().resolve())
                if codex_config_dir is not None
                else None
            ),
        )
        with self._lock:
            data = self._read_all()
            data[self._key(binding.runtime, native_session_id)] = record.to_dict()
            self._write_all(data)

    def get(
        self, runtime: Runtime, native_session_id: str
    ) -> FrozenSessionConfiguration | None:
        """读取原生会话最近保存的冻结条件；单条损坏记录保守返回 None。"""

        with self._lock:
            raw = self._read_all().get(self._key(runtime, native_session_id))
        if not isinstance(raw, dict):
            return None
        try:
            return FrozenSessionConfiguration(
                runtime=Runtime(str(raw["runtime"])),
                native_session_id=str(raw["native_session_id"]),
                connection_id=(
                    str(raw["connection_id"])
                    if raw.get("connection_id") is not None
                    else None
                ),
                connection_identity_version=(
                    int(raw["connection_identity_version"])
                    if isinstance(raw.get("connection_identity_version"), int)
                    and not isinstance(raw.get("connection_identity_version"), bool)
                    else None
                ),
                model=str(raw["model"]) if raw.get("model") is not None else None,
                effort=(str(raw["effort"]) if raw.get("effort") is not None else None),
                permission=(
                    str(raw["permission"])
                    if raw.get("permission") is not None
                    else None
                ),
                permission_preset=(
                    str(raw["permission_preset"])
                    if raw.get("permission_preset") is not None
                    else None
                ),
                memory_enabled=bool(raw.get("memory_enabled", True)),
                memory_mcp_enabled=bool(
                    raw.get(
                        "memory_mcp_enabled",
                        bool(raw.get("memory_enabled", True))
                        and raw.get("connection_id") is None,
                    )
                ),
                profile_enabled=bool(raw.get("profile_enabled", True)),
                self_enabled=bool(raw.get("self_enabled", True)),
                # 旧档案没有该字段时按 capability-closed 处理，不能猜测开启。
                agent_mcp_enabled=bool(raw.get("agent_mcp_enabled", False)),
                claude_auto_memory_disabled=bool(
                    raw.get("claude_auto_memory_disabled", False)
                ),
                claude_config_dir=(
                    str(raw["claude_config_dir"])
                    if raw.get("claude_config_dir") is not None
                    else None
                ),
                codex_config_dir=(
                    str(raw["codex_config_dir"])
                    if raw.get("codex_config_dir") is not None
                    else None
                ),
                delegation_targets=tuple(
                    target
                    for item in raw.get("delegation_targets", ())
                    if (target := DelegationTarget.from_dict(item)) is not None
                )
                if isinstance(raw.get("delegation_targets", ()), (list, tuple))
                else (),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _key(runtime: Runtime, native_session_id: str) -> str:
        """构造不会混淆两种 runtime 原生 ID 的档案键。"""

        return f"{runtime.value}:{native_session_id}"

    def _read_all(self) -> dict[str, object]:
        """读取完整档案；缺失时返回空对象，损坏时拒绝继续读写。"""

        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigurationArchiveCorruptError(
                f"native configuration archive is unreadable: {self.path}"
            ) from exc
        if not isinstance(value, dict):
            raise ConfigurationArchiveCorruptError(
                f"native configuration archive is not an object: {self.path}"
            )
        return value

    def _write_all(self, data: dict[str, object]) -> None:
        """在同目录写入临时文件后原子替换档案。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent
        )
        temp_path = Path(raw_path)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
            temp_path.chmod(0o600)
            temp_path.replace(self.path)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise


def resolve_configuration_archive_path(binding_path: Path) -> Path:
    """把 binding 文件路径转换成同目录的原生配置档案路径。"""

    return binding_path.with_name(f"{binding_path.stem}-native-configurations.json")
