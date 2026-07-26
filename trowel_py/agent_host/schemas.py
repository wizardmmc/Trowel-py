"""``/api/agent`` 路由的输入 wire shape。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

RuntimeWire = Literal["claude_code", "codex"]
PermissionPreset = Literal[
    "follow", "read-only", "workspace-write", "danger-full-access"
]
GoalStatus = Literal[
    "active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
    session_kind: Literal[
        "user", "delegate", "default", "incubation", "maintenance", "experiment"
    ] = "user"
    session_purpose: Literal[
        "foreground", "default", "incubation", "maintenance", "experiment"
    ] = "foreground"
    memory_eligibility: bool = Field(default=True, strict=True)
    memory_eligibility_mode: Literal["eligible", "ineligible", "adopted"] = "eligible"
    agent_mcp_enabled: bool = Field(default=True, strict=True)
    model_os_mcp_enabled: bool = Field(default=False, strict=True)
    native_tools_mode: Literal["default", "none"] = "default"
    parent_session_id: str | None = None
    delegation_depth: int = Field(default=0, ge=0, le=1)


class PatchAgentSessionRequest(BaseModel):
    """``runtime`` 创建后不可变；model/effort 只为下一次 Codex turn 排队。

    ``permission_preset`` 立即写入 binding 的 requested 字段，并在下一次
    ``turn/start`` 作为 ``sandboxPolicy``/``approvalPolicy`` override 生效。
    """

    runtime: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_preset: PermissionPreset | None = None


class SendMessageBody(BaseModel):
    text: str = Field(min_length=1)


class SetCodexGoalRequest(BaseModel):
    objective: str | None = Field(default=None, min_length=1)
    status: GoalStatus | None = None
    token_budget: int | None = Field(default=None, ge=1)


class UncommittedChangesReviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["uncommittedChanges"]


class BaseBranchReviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["baseBranch"]
    branch: NonEmptyText


class CommitReviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["commit"]
    sha: NonEmptyText
    title: NonEmptyText | None = None


class CustomReviewTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["custom"]
    instructions: NonEmptyText


CodexReviewTarget = Annotated[
    UncommittedChangesReviewTarget
    | BaseBranchReviewTarget
    | CommitReviewTarget
    | CustomReviewTarget,
    Field(discriminator="type"),
]


class StartCodexReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: CodexReviewTarget


class AnswerAgentRequest(BaseModel):
    """回答一个 connection-scoped Codex server request。

    HTTP 边界保留原始 decision 字符串；manager 再根据该 pending request 记录的
    ``availableDecisions`` 校验。
    """

    decision: str = Field(min_length=1)
