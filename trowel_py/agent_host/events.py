"""两个 runtime 共用的 AgentEvent v1 wire contract。

CC 与 Codex adapter 负责各自的事件语义和 payload；本层只固定路由、关联与顺序
字段。两种 adapter 都为实际发出的事件分配会话内连续序号，原生序号不会直接进入
共享 envelope。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trowel_py.cc_host.schemas import EVENT_TYPES as _CC_EVENT_TYPES

# 每个 envelope 都携带版本判别符，使未来协议升级可以在边界被识别。
AGENT_EVENT_SCHEMA: Literal["agent-event-v1"] = "agent-event-v1"

# 不能直接复用 CC type 的稳定 Codex 事件使用明确扩展名，不能塞进泛化 payload。
_CODEX_EXTENSION_TYPES: frozenset[str] = frozenset(
    {
        # ``thread/tokenUsage/updated`` 的 turn 级 token 统计。
        "usage_updated",
        # manager 根据 transport 状态合成的生命周期，不对应单条原生通知。
        "host_status",
        # connection-scoped Codex 审批生命周期。
        "approval_request",
        # ``account/rateLimits/updated`` 的账户级额度快照。
        "rate_limit_updated",
        # ``contextCompaction`` 只在 completed 时形成新的上下文代际边界。
        "compaction",
    }
)

# 直接合并 CC 词汇，避免新增 CC 事件时在共享 envelope 维护第二份名单。
AGENT_EVENT_TYPES: frozenset[str] = _CC_EVENT_TYPES | _CODEX_EXTENSION_TYPES

# 在 leaf schema 中重复声明，避免 wire contract 反向依赖 runtime 层。
AgentRuntime = Literal["claude_code", "codex"]


class AgentEvent(BaseModel):
    """live stream 与 history replay 共用的 host-neutral envelope。

    ``seq`` 只在同一 session 内比较，用于去重和发现缺口；``turn_id`` 与
    ``item_id`` 在原生协议提供时保持关联语义。payload 的逐类型校验由各 runtime
    translator 负责，本模型只拒绝共享词汇之外的 ``type``。
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["agent-event-v1"] = Field(
        default=AGENT_EVENT_SCHEMA, alias="schema"
    )
    session_id: str = Field(min_length=1)
    runtime: AgentRuntime
    seq: int = Field(ge=1)
    type: str
    turn_id: str | None = None
    item_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type_in_vocabulary(cls, value: str) -> str:
        """未知 type 表示 adapter 映射缺口，不能作为透传事件进入共享边界。"""

        if value not in AGENT_EVENT_TYPES:
            raise ValueError(
                f"unknown agent event type {value!r}; not in AGENT_EVENT_TYPES"
            )
        return value
