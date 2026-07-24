"""Codex session 的冻结配置与原生 thread 事实。"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.protocol import TROWEL_NOTE_SEARCH_SERVER_NAME


@dataclass(frozen=True)
class TrowelMemoryMcpConfig:
    """附加到 Codex thread 的 Trowel memory MCP 配置。

    fresh thread 尚无原生 thread id，native_session_id 必须为空，不能用
    Trowel 会话 id 冒充；resume 时才写入绑定中的真实 thread id。
    """

    server_name: str
    command: str
    module_args: tuple[str, ...]
    memory_root: str
    trowel_session_id: str

    def to_thread_config(self, *, native_session_id: str = "") -> dict[str, Any]:
        """构造 mcp_servers 配置；服务必需且本地 memory 工具预先授权。"""

        return {
            self.server_name: {
                "command": self.command,
                "args": list(self.module_args),
                "env": {
                    "MEMORY_ROOT": self.memory_root,
                    "TROWEL_SESSION_ID": self.trowel_session_id,
                    "TROWEL_HOST_KIND": "codex",
                    "TROWEL_NATIVE_SESSION_ID": native_session_id,
                },
                "required": True,
                "enabled_tools": ["search", "read", "outcome"],
                "default_tools_approval_mode": "approve",
            }
        }


def build_default_trowel_memory_mcp(
    *,
    trowel_session_id: str,
    memory_root: str,
    server_name: str = TROWEL_NOTE_SEARCH_SERVER_NAME,
) -> TrowelMemoryMcpConfig:
    """用当前解释器构造标准 Trowel memory MCP 配置。"""

    import sys

    return TrowelMemoryMcpConfig(
        server_name=server_name,
        command=sys.executable,
        module_args=("-m", "trowel_py.memory.mcp_server"),
        memory_root=str(memory_root),
        trowel_session_id=trowel_session_id,
    )


@dataclass(frozen=True)
class CodexSessionConfig:
    """定义 Codex 会话的冻结输入。

    developer_instructions 会覆盖用户 Codex 配置中的同名值，并非追加；
    ephemeral=False 保留可供 app-server 重启后恢复的 native rollout；
    memory MCP 为 None 时，本会话不附加 Trowel memory MCP。
    """

    trowel_session_id: str
    workdir: str
    model: str | None = None
    effort: str | None = None
    developer_instructions: str | None = None
    approval_policy: str | None = None
    sandbox: str | None = None
    ephemeral: bool = False
    initial_thread_id: str | None = None
    trowel_memory_mcp: TrowelMemoryMcpConfig | None = None


@dataclass(frozen=True)
class ThreadBinding:
    """会话当前绑定的原生事实；值对象不可变。"""

    thread_id: str
    model: str
    model_provider: str
    cwd: str
    sandbox: Mapping[str, Any]
    approval_policy: str | Mapping[str, Any] | None
    permission_profile: str | None = None
    effective_sandbox: str | None = None
    effective_approval: str | None = None
    network_access: bool | None = None
    service_tier: str | None = None
    reasoning_effort: str | None = None


def _wire_mode(value: object) -> str | None:
    """归一化已知模式，不猜测未知值。"""

    if not isinstance(value, str) or not value:
        return None
    known = {
        "readOnly": "read-only",
        "workspaceWrite": "workspace-write",
        "dangerFullAccess": "danger-full-access",
        "externalSandbox": "external-sandbox",
    }
    return known.get(value, value)


def _sandbox_facts(value: object) -> tuple[str | None, bool | None]:
    """提取原生 sandbox 模式与网络权限。"""

    if not isinstance(value, Mapping):
        return None, None
    mode = _wire_mode(value.get("type") or value.get("mode"))
    raw_network = value.get("networkAccess")
    if isinstance(raw_network, bool):
        network = raw_network
    elif raw_network == "enabled":
        network = True
    elif raw_network == "restricted":
        network = False
    elif mode == "danger-full-access":
        # dangerFullAccess 即使省略 networkAccess 也明确允许网络。
        network = True
    else:
        network = None
    return mode, network


def _approval_fact(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping) and isinstance(value.get("policy"), str):
        return str(value["policy"])
    return None


def _permission_profile_fact(value: object) -> str | None:
    if isinstance(value, Mapping) and isinstance(value.get("id"), str):
        return str(value["id"])
    return None


def parse_thread_binding(result: Mapping[str, Any]) -> ThreadBinding:
    """解析原生 thread 事实；缺少 id、model、provider 或 cwd 时拒绝响应。

    原始策略映射使用只读代理保存，公开字段另行归一化。
    """

    thread = result.get("thread")
    if not isinstance(thread, Mapping) or not thread.get("id"):
        raise ProtocolViolationError(
            "thread/start response has no thread.id",
            payload=dict(result),
        )
    for required in ("model", "modelProvider", "cwd"):
        if required not in result:
            raise ProtocolViolationError(
                f"thread response missing effective fact {required!r}",
                payload=dict(result),
            )
    sandbox = result.get("sandbox")
    approval_policy = result.get("approvalPolicy")
    effective_sandbox, network_access = _sandbox_facts(sandbox)
    effective_approval = _approval_fact(approval_policy)
    permission_profile = _permission_profile_fact(result.get("activePermissionProfile"))
    return ThreadBinding(
        thread_id=str(thread["id"]),
        model=str(result["model"]),
        model_provider=str(result["modelProvider"]),
        cwd=str(result["cwd"]),
        sandbox=MappingProxyType(dict(sandbox))
        if isinstance(sandbox, Mapping)
        else MappingProxyType({}),
        approval_policy=(
            MappingProxyType(dict(approval_policy))
            if isinstance(approval_policy, Mapping)
            else str(approval_policy)
            if isinstance(approval_policy, str)
            else None
        ),
        permission_profile=permission_profile,
        effective_sandbox=effective_sandbox,
        effective_approval=effective_approval,
        network_access=network_access,
        service_tier=str(result["serviceTier"])
        if result.get("serviceTier") is not None
        else None,
        reasoning_effort=str(result["reasoningEffort"])
        if result.get("reasoningEffort") is not None
        else None,
    )


# 这些对象长期从 session.py 导入；保留原 FQN 以兼容 pickle 与类型诊断。
_PUBLIC_SESSION_MODULE = "trowel_py.codex_host.session"
for _public_symbol in (
    TrowelMemoryMcpConfig,
    CodexSessionConfig,
    ThreadBinding,
    build_default_trowel_memory_mcp,
    parse_thread_binding,
):
    _public_symbol.__module__ = _PUBLIC_SESSION_MODULE
del _public_symbol
