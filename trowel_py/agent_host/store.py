"""在 JSON 文件中持久化 Agent Host 会话绑定。"""

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
    """返回 Agent Host 会话绑定文件的路径。

    环境变量 ``TROWEL_AGENT_SESSIONS_PATH`` 非空时使用其展开用户目录后的值，
    否则使用 ``~/.trowel/agent_sessions.json``。

    Returns:
        当前配置的会话绑定文件路径。
    """

    override = os.environ.get("TROWEL_AGENT_SESSIONS_PATH")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PATH


class BindingStore:
    """读写 Trowel 会话与原生运行时会话的持久化绑定。

    每次操作都重新读取文件。单次写入通过替换同目录中的临时文件完成，但整个
    读改写过程不提供跨进程事务；多个进程同时写入时，后完成的进程可能覆盖先前
    进程的改动。

    Attributes:
        path: 保存全部会话绑定的 JSON 文件路径。
    """

    def __init__(self, path: Path) -> None:
        """创建使用指定 JSON 文件的会话绑定存储。

        构造时不读写文件；路径的父目录可以不存在，首次写入时会自动创建。

        Args:
            path: 保存全部会话绑定的 JSON 文件路径。
        """

        self._path = path

    @property
    def path(self) -> Path:
        """返回保存全部会话绑定的 JSON 文件路径。"""

        return self._path

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        """读取持久化文件中的原始会话映射。

        文件不存在、读取时发生 ``OSError``、JSON 语法无效，或根节点和
        ``sessions`` 不是对象时，返回空映射。``sessions`` 中不是对象的条目会被
        跳过；其余对象保持原样，由 ``get()`` 或 ``list_all()`` 转换为会话绑定。

        Returns:
            以 Trowel 会话 ID 为键的原始绑定数据。
        """

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
        """通过同目录临时文件原子替换持久化文件。

        Args:
            sessions: 以 Trowel 会话 ID 为键的完整绑定数据。
        """

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
            # 即使收到 KeyboardInterrupt 或 SystemExit，也尽量清理临时文件。
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def put(self, binding: SessionBinding) -> None:
        """按 Trowel 会话 ID 新增或覆盖一个会话绑定。

        Args:
            binding: 要持久化的完整会话绑定。
        """

        sessions = self._load_raw()
        sessions[binding.session_id] = binding.to_dict()
        self._save_raw(sessions)

    def get(self, session_id: str) -> SessionBinding | None:
        """按 Trowel 会话 ID 读取一个会话绑定。

        Args:
            session_id: Agent Host 管理的会话 ID。

        Returns:
            已转换的会话绑定；没有对应记录时返回 ``None``。

        Raises:
            KeyError: 持久化记录缺少必填字段。
            ValueError: 持久化记录包含未知的运行工具名称。
        """

        raw = self._load_raw().get(session_id)
        return binding_from_dict(raw) if raw is not None else None

    def list_all(self) -> list[SessionBinding]:
        """读取当前文件中的全部会话绑定。

        Returns:
            当前持久化的会话绑定列表。

        Raises:
            KeyError: 任一持久化记录缺少必填字段。
            ValueError: 任一持久化记录包含未知的运行工具名称。
        """

        return [binding_from_dict(payload) for payload in self._load_raw().values()]

    def delete(self, session_id: str) -> bool:
        """删除指定的会话绑定。

        Args:
            session_id: Agent Host 管理的会话 ID。

        Returns:
            记录原本存在并已删除时返回 ``True``，不存在时返回 ``False``。
        """

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
        """更新运行时报告的会话状态并刷新更新时间。

        每个可选参数传入 ``None`` 都表示保留对应字段的原值，因此此方法不能清空
        可空字段。即使没有字段需要更新，``updated_at`` 仍会刷新。

        Args:
            session_id: Agent Host 管理的会话 ID。
            native_session_id: Claude Code 会话 ID 或 Codex thread ID。
            model: 运行时实际使用的模型。
            effort: 运行时实际使用的思考强度。
            permission: Claude Code 权限模式或 Codex 实际权限的简要说明。
            connected: 要持久化的运行时连接状态。
            running: 要持久化的运行状态。
            effective_permission_profile: Codex 报告的实际权限配置名称。
            effective_sandbox: Codex 报告的实际沙箱模式。
            effective_approval: Codex 报告的实际操作确认策略。
            network_access: Codex 报告的网络访问状态。

        Returns:
            更新并持久化后的新会话绑定实例。

        Raises:
            KeyError: 找不到指定绑定，或持久化记录缺少必填字段。
            ValueError: 持久化记录包含未知的运行工具名称。
        """

        existing = self.get(session_id)
        if existing is None:
            raise KeyError(session_id)
        changes: dict[str, Any] = {
            "updated_at": datetime.now().isoformat(timespec="microseconds")
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
