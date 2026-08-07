"""``/api/agent`` 路由的输入 wire shape。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from trowel_py.agent_host.binding import SessionKind

RuntimeWire = Literal["claude_code", "codex"]
PermissionPreset = Literal[
    "follow", "read-only", "workspace-write", "danger-full-access"
]
GoalStatus = Literal[
    "active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"
]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
SessionTitleText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=80,
        pattern=r"^[^\r\n]+$",
    ),
]
ResumeTitleText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
]


class CreateAgentSessionRequest(BaseModel):
    """创建一种 runtime 的会话。

    ``runtime`` 与三个注入开关在恢复同一原生会话时保持不变。CC 使用
    ``permission_mode``；Codex 优先使用 ``permission_preset``，并继续接受旧调用方
    直接传入的 ``approval_policy`` 与 ``sandbox``。
    """

    runtime: RuntimeWire
    connection_id: str | None = None
    workdir: str = Field(min_length=1)
    resume_from: str | None = None
    resume_title: ResumeTitleText | None = None
    model: str | None = None
    effort: str | None = None
    permission_mode: str | None = None
    approval_policy: str | None = None
    sandbox: str | None = None
    permission_preset: PermissionPreset | None = None
    memory_enabled: bool = Field(default=True, strict=True)
    profile_enabled: bool = Field(default=True, strict=True)
    self_enabled: bool = Field(default=True, strict=True)
    session_kind: SessionKind = "user"
    memory_eligibility: bool = Field(default=True, strict=True)
    agent_mcp_enabled: bool = Field(default=True, strict=True)
    parent_session_id: str | None = None
    delegation_depth: int = Field(default=0, ge=0, le=1)
    owner_ref: str | None = Field(default=None, min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_internal_owner(self) -> CreateAgentSessionRequest:
        """要求 discussion 私有会话带 owner_ref。

        Returns:
            校验通过的原请求。

        Raises:
            ValueError: discussion 未带 owner_ref。
        """

        if self.session_kind == "discussion" and self.owner_ref is None:
            raise ValueError("discussion session requires owner_ref")
        return self


class PatchAgentSessionRequest(BaseModel):
    """修改已创建的 Codex 会话配置。

    模型和思考强度先为下一轮排队，Codex 接受该轮后成为对话线程的当前设置。权限
    模式立即写入会话记录，并从下一轮或下次重连起生效；此接口不能切换到 follow。
    运行工具创建后不能更换。

    Attributes:
        runtime: 用于确认会话仍使用原运行工具；传入另一种运行工具会被拒绝，None
            表示不检查。
        model: 下一轮请求使用的 Codex 模型；None 表示保持现有选择。
        effort: 下一轮请求使用的 Codex 思考强度；None 表示保持现有选择。
        permission_preset: 后续轮次和重连使用的 Codex 权限模式；None 表示保持现有
            选择，follow 会被拒绝。
    """

    runtime: str | None = None
    model: str | None = None
    effort: str | None = None
    permission_preset: PermissionPreset | None = None


class SendMessageBody(BaseModel):
    """携带要发送给 Agent 会话的非空文本。

    Attributes:
        text: 作为本轮用户输入发送给 Claude Code 或 Codex 的文字。
    """

    text: str = Field(min_length=1)


class StartInteractiveDelegationRequest(BaseModel):
    """携带 Agent MCP 已复核的父会话和 child 创建参数。

    Attributes:
        parent_session_id: 发起委派的父 Trowel 会话 ID。
        task: 交给 Claude Code child 的非空任务正文。
        create_body: Agent MCP 根据父 binding 生成的 child 会话创建请求。
    """

    model_config = ConfigDict(extra="forbid")

    parent_session_id: NonEmptyText
    task: NonEmptyText
    create_body: dict[str, Any]


class AnswerInteractiveDelegationRequest(BaseModel):
    """携带委派归属和 Claude Code AskUserQuestion 的答案。

    Attributes:
        parent_session_id: 当前 MCP 进程声明的父 Trowel 会话 ID。
        answers: 以完整问题或唯一标题为键的答案映射。
    """

    model_config = ConfigDict(extra="forbid")

    parent_session_id: NonEmptyText
    answers: dict[str, str]


class RenameAgentSessionRequest(BaseModel):
    """携带用户手动指定的会话标题。

    Attributes:
        title: 去除首尾空白后的非空标题，最多 80 个字符。
    """

    title: SessionTitleText


class RememberWorkspaceRequest(BaseModel):
    """携带一次用户确认打开的 Agent 工作区。

    Attributes:
        path: 用户选择的本地目录路径；仓储负责展开、规范化和可用性校验。
    """

    model_config = ConfigDict(extra="forbid")

    path: NonEmptyText


class GenerateAgentSessionTitleRequest(BaseModel):
    """携带用于生成语义标题的首条用户消息。

    Attributes:
        text: 主会话收到的首条非空用户输入；标题任务只概括它，不执行它。
    """

    text: NonEmptyText


class SetCodexGoalRequest(BaseModel):
    """指定要创建或更新的 Codex Goal 字段。

    Attributes:
        objective: Goal 要完成的任务；None 表示不修改。
        status: Goal 当前的执行状态；None 表示不修改。
        token_budget: Goal 最多可以使用的 token 数量。省略该字段时保留原限制；
            明确传入 None 时取消限制。
    """

    objective: str | None = Field(default=None, min_length=1)
    status: GoalStatus | None = None
    token_budget: int | None = Field(default=None, ge=1)


class UncommittedChangesReviewTarget(BaseModel):
    """要求 Codex 审查当前工作区中尚未提交的全部改动。

    Attributes:
        type: 固定为 uncommittedChanges，用于选择这种审查目标。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["uncommittedChanges"]


class BaseBranchReviewTarget(BaseModel):
    """要求 Codex 审查当前分支相对于指定基础分支的改动。

    Attributes:
        type: 固定为 baseBranch，用于选择这种审查目标。
        branch: 作为比较基准的分支名称。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["baseBranch"]
    branch: NonEmptyText


class CommitReviewTarget(BaseModel):
    """要求 Codex 审查指定 Git 提交引入的改动。

    Attributes:
        type: 固定为 commit，用于选择这种审查目标。
        sha: 要审查的 Git 提交 ID。
        title: 随审查请求提供的可选提交标题；None 表示不提供标题。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["commit"]
    sha: NonEmptyText
    title: NonEmptyText | None = None


class CustomReviewTarget(BaseModel):
    """要求 Codex 按给定范围和关注点进行审查。

    Attributes:
        type: 固定为 custom，用于选择这种审查目标。
        instructions: 描述审查范围和要求的非空文字。
    """

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
    """携带一次 Codex 代码审查的目标。

    Attributes:
        target: 要审查的内容，可以是未提交改动、与基础分支的差异、指定提交或
            自定义要求。
    """

    model_config = ConfigDict(extra="forbid")

    target: CodexReviewTarget


class AnswerAgentRequest(BaseModel):
    """回答当前 Codex 连接上的一个待决请求。

    HTTP 边界保留原始决定文本，运行时管理器再按该请求允许的选项校验。

    Attributes:
        decision: 用户选择的处理方式，必须与该请求允许的决定之一匹配。
    """

    decision: str = Field(min_length=1)
