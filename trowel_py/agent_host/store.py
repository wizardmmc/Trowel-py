"""以 JSON 持久化会话 binding，并通过原子文件替换避免半写状态。"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.agent_host.binding import SessionBinding, binding_from_dict

_SCHEMA_VERSION = 1
_DEFAULT_PATH = Path.home() / ".trowel" / "agent_sessions.json"


def resolve_bindings_path() -> Path:
    """优先使用 ``TROWEL_AGENT_SESSIONS_PATH`` 指定的文件。"""

    override = os.environ.get("TROWEL_AGENT_SESSIONS_PATH")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PATH


class BindingStore:
    """每次操作都重新读盘以避免实例缓存陈旧，但不提供跨进程读改写事务。"""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        """文件级损坏回落为空集合；``sessions`` 中非字典条目则单独跳过。"""

        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            return {}
        return {
            sid: payload
            for sid, payload in sessions.items()
            if isinstance(payload, dict)
        }

    def _save_raw(self, sessions: dict[str, dict[str, Any]]) -> None:
        """在目标目录写临时文件，再以 ``os.replace`` 原子替换单个版本。"""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _SCHEMA_VERSION, "sessions": sessions}
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            # 写入失败时清理临时片段；原文件要么已整体替换，要么保持不变。
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def put(self, binding: SessionBinding) -> None:
        sessions = self._load_raw()
        sessions[binding.session_id] = binding.to_dict()
        self._save_raw(sessions)

    def get(self, session_id: str) -> SessionBinding | None:
        raw = self._load_raw().get(session_id)
        return binding_from_dict(raw) if raw is not None else None

    def list_all(self) -> list[SessionBinding]:
        return [binding_from_dict(payload) for payload in self._load_raw().values()]

    def delete(self, session_id: str) -> bool:
        sessions = self._load_raw()
        if session_id not in sessions:
            return False
        del sessions[session_id]
        self._save_raw(sessions)
        return True

    def update_native(
        self,
        session_id: str,
        *,
        native_session_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission: str | None = None,
        connected: bool | None = None,
        running: bool | None = None,
        effective_permission_profile: str | None = None,
        effective_sandbox: str | None = None,
        effective_approval: str | None = None,
        network_access: bool | None = None,
    ) -> SessionBinding:
        """以新实例写回原生事实；``None`` 表示不更新，不能用于清空可空字段。"""

        existing = self.get(session_id)
        if existing is None:
            raise KeyError(session_id)
        changes: dict[str, Any] = {
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }
        if native_session_id is not None:
            changes["native_session_id"] = native_session_id
        if model is not None:
            changes["model"] = model
        if effort is not None:
            changes["effort"] = effort
        if permission is not None:
            changes["permission"] = permission
        if connected is not None:
            changes["connected"] = connected
        if running is not None:
            changes["running"] = running
        if effective_permission_profile is not None:
            changes["effective_permission_profile"] = effective_permission_profile
        if effective_sandbox is not None:
            changes["effective_sandbox"] = effective_sandbox
        if effective_approval is not None:
            changes["effective_approval"] = effective_approval
        if network_access is not None:
            changes["network_access"] = network_access
        updated = replace(existing, **changes)
        self.put(updated)
        return updated
