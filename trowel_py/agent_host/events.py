"""定义 Claude Code 与 Codex 共用的 AgentEvent 事件格式。

两种运行工具各自负责转换事件类型和内容；本模块只规定事件所属的 Trowel 会话、
Codex thread、轮次或工具调用，以及发送顺序。事件序号在每个 Trowel 会话内连续
生成，不直接使用 Claude Code 或 Codex 自带的序号。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trowel_py.cc_host.schemas import EVENT_TYPES as _CC_EVENT_TYPES

# 每个 AgentEvent 都带格式版本，接收方可在格式升级后选择对应的解析规则。
AGENT_EVENT_SCHEMA: Literal["agent-event-v1"] = "agent-event-v1"

# Codex 特有事件保留各自的明确类型，避免接收方根据内容字段猜测事件含义。
_CODEX_EXTENSION_TYPES: frozenset[str] = frozenset(
    {
        # Codex thread/tokenUsage/updated 通知中的本轮和累计 token 用量。
        "usage_updated",
        # 根据 Codex 进程和连接状态生成，不对应 Codex 发来的一条通知。
        "host_status",
        # 只在当前 Codex 连接中有效的操作确认请求；重连后旧请求失效。
        "approval_request",
        # Codex account/rateLimits/updated 通知中的账户额度信息。
        "rate_limit_updated",
        # 只在上下文压缩完成后发出，表示后续轮次使用压缩后的上下文。
        "compaction",
        "goal_updated",
        "goal_cleared",
        "plan_updated",
        "turn_diff_updated",
        "subagent_activity",
    }
)

# 通用事件类型由 Claude Code 已有类型和 Codex 特有类型合并而成；
# Claude Code 新增的事件类型也会随之加入。
AGENT_EVENT_TYPES: frozenset[str] = _CC_EVENT_TYPES | _CODEX_EXTENSION_TYPES

# 在事件模型中单独声明允许值，避免为了这两个值依赖上层的会话管理模块。
AgentRuntime = Literal["claude_code", "codex"]


class AgentEvent(BaseModel):
    """表示 Claude Code 与 Codex 在实时事件流和历史回放中共用的事件。

    事件内容由对应运行工具的转换代码负责校验；本模型只检查事件类型是否已经登记。

    Attributes:
        schema_version: 事件格式版本；序列化后的字段名为 schema，当前固定为
            "agent-event-v1"。
        session_id: 事件所属的 Trowel 会话 ID。
        runtime: 产生事件的运行工具，值为 "claude_code" 或 "codex"。
        seq: 该 Trowel 会话实际发出事件的连续序号，从 1 开始，用于去重和发现
            事件缺失。
        type: 事件类型，必须属于 AGENT_EVENT_TYPES。
        thread_id: Codex thread ID；Claude Code 事件不使用该字段。
        turn_id: 事件所属的轮次 ID。
        item_id: 事件关联的工具调用或其他 Codex 条目 ID，用于关联同一条目的
            启动、更新和完成事件。
        payload: 随事件类型变化的具体内容。
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Literal["agent-event-v1"] = Field(
        default=AGENT_EVENT_SCHEMA, alias="schema"
    )
    session_id: str = Field(min_length=1)
    runtime: AgentRuntime
    seq: int = Field(ge=1)
    type: str
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type_in_vocabulary(cls, value: str) -> str:
        """只接受已经登记在 AGENT_EVENT_TYPES 中的事件类型。"""

        if value not in AGENT_EVENT_TYPES:
            raise ValueError(
                f"unknown agent event type {value!r}; not in AGENT_EVENT_TYPES"
            )
        return value
