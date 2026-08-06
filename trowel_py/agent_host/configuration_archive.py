"""持久保存原生会话的脱敏冻结条件，供 binding 删除后继续恢复。"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from trowel_py.agent_host.binding import Runtime, SessionBinding


class ConfigurationArchiveCorruptError(RuntimeError):
    """表示冻结配置档案整体损坏，继续写入会覆盖仍可抢救的历史。"""


@dataclass(frozen=True)
class FrozenSessionConfiguration:
    """保存恢复原生会话所需且不含凭据的创建条件。"""

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
        }


class SessionConfigurationArchive:
    """按 runtime 和原生会话 ID 原子读写脱敏冻结条件。"""

    def __init__(self, path: Path) -> None:
        """保存档案路径；文件不存在时按空档案处理。"""

        self.path = path
        self._lock = threading.Lock()

    def put(self, binding: SessionBinding) -> None:
        """在 binding 已取得原生 ID 后保存或覆盖冻结条件。"""

        native_session_id = binding.native_session_id
        if not native_session_id:
            return
        record = FrozenSessionConfiguration(
            runtime=binding.runtime,
            native_session_id=native_session_id,
            connection_id=binding.connection_id,
            connection_identity_version=binding.connection_identity_version,
            model=binding.model,
            effort=binding.effort,
            permission=binding.permission,
            permission_preset=binding.permission_preset,
            memory_enabled=binding.memory_enabled,
            memory_mcp_enabled=binding.memory_mcp_enabled,
            profile_enabled=binding.profile_enabled,
            self_enabled=binding.self_enabled,
            agent_mcp_enabled=binding.agent_mcp_enabled,
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
                effort=(
                    str(raw["effort"]) if raw.get("effort") is not None else None
                ),
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
