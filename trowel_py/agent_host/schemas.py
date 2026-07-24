"""``/api/agent`` 路由的输入 wire shape。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RuntimeWire = Literal["claude_code", "codex"]
PermissionPreset = Literal[
    "follow", "read-only", "workspace-write", "danger-full-access"
]


class CreateAgentSessionRequest(BaseModel):
    """创建一种 runtime 的会话。

    ``runtime`` 与三个注入开关在恢复同一原生会话时保持不变。CC 使用
    ``permission_mode``；Codex 优先使用 ``permission_preset``，并继续接受旧调用方
    直接传入的 ``approval_policy`` 与 ``sandbox``。
    """

    runtime: RuntimeWire
    workdir: str = Field(min_length=1)
    resume_from: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    approval_policy: str | None = None
    sandbox: str | None = None
    permission_preset: PermissionPreset | None = None
    memory_enabled: bool = Field(default=True, strict=True)
    profile_enabled: bool = Field(default=True, strict=True)
    self_enabled: bool = Field(default=True, strict=True)


class PatchAgentSessionRequest(BaseModel):
    """``runtime`` 创建后不可变；model/effort 只为下一次 Codex turn 排队。"""

    runtime: str | None = None
    model: str | None = None
    effort: str | None = None


class SendMessageBody(BaseModel):
    text: str = Field(min_length=1)


class AnswerAgentRequest(BaseModel):
    """回答一个 connection-scoped Codex server request。

    HTTP 边界保留原始 decision 字符串；manager 再根据该 pending request 记录的
    ``availableDecisions`` 校验。
    """

    decision: str = Field(min_length=1)
