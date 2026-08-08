"""定义 discussion HTTP 命令的公开 wire shape。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ClaudePermissionMode = Literal[
    "bypassPermissions",
    "default",
    "acceptEdits",
    "dontAsk",
]
CodexPermissionPreset = Literal[
    "follow",
    "read-only",
    "workspace-write",
    "danger-full-access",
]
CommandId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]


class ParticipantRequest(BaseModel):
    """选择一位 participant 的名称和完整会话配置。

    Attributes:
        name: 研讨内唯一的用户可见名称。
        session_configuration_id: 可选的设置域命名会话配置 ID。
        connection_id: 未使用命名配置时直接选择的连接 ID。
        model: 未使用命名配置时直接选择的模型 ID。
        effort: 未使用命名配置时直接选择的思考强度。
        permission_mode: Claude Code 创建时冻结的权限模式。
        permission_preset: Codex 创建时冻结的权限预设。
        memory_enabled: 是否向参与者注入 Trowel Memory。
        profile_enabled: 是否向参与者注入已确认的用户画像。
        self_enabled: 是否向参与者注入 Trowel 持续主体说明。
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=60,
            pattern=r"^[^\r\n]+$",
        ),
    ]
    session_configuration_id: NonEmptyText | None = None
    connection_id: NonEmptyText | None = None
    model: NonEmptyText | None = None
    effort: Annotated[
        str,
        StringConstraints(strip_whitespace=True, max_length=32),
    ] | None = None
    permission_mode: ClaudePermissionMode | None = None
    permission_preset: CodexPermissionPreset | None = None
    memory_enabled: bool = Field(default=True, strict=True)
    profile_enabled: bool = Field(default=True, strict=True)
    self_enabled: bool = Field(default=True, strict=True)

    @model_validator(mode="after")
    def require_one_configuration_source(self) -> ParticipantRequest:
        """要求命名配置或直接连接组合二选一。

        Returns:
            配置来源完整且不冲突的参与者请求。

        Raises:
            ValueError: 两种来源同时出现，或直接组合缺少连接与模型。
        """

        has_inline = self.connection_id is not None or self.model is not None
        if self.session_configuration_id is not None:
            if has_inline or self.effort is not None:
                raise ValueError("named and inline participant configuration conflict")
            return self
        if self.connection_id is None or self.model is None:
            raise ValueError("participant requires a saved configuration or connection/model")
        return self


class CreateDiscussionRequest(BaseModel):
    """创建并冻结一场研讨的初始议题、模式和参与者。

    Attributes:
        request_id: 创建请求的客户端稳定身份。
        topic: 初始议题原话，同时登记为第一条 Profile 用户来源。
        workdir: participant 原生工具统一使用的项目目录。
        progression_mode: automatic 或 user_guided。
        max_rounds: 自动模式默认 3，可由用户设置；用户参与模式必须为空。
        participants: 按创建顺序冻结的 2 至 8 位参与者。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: CommandId
    topic: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)
    ]
    workdir: NonEmptyText
    progression_mode: Literal["automatic", "user_guided"] = "automatic"
    max_rounds: int | None = Field(default=None, ge=1, le=100)
    participants: list[ParticipantRequest] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def normalize_round_limit(self) -> CreateDiscussionRequest:
        """落实自动默认三轮、用户参与模式无上限的产品决定。

        Returns:
            自动模式已补默认值的请求。

        Raises:
            ValueError: 用户参与模式传入了最大轮数。
        """

        if self.progression_mode == "user_guided":
            if self.max_rounds is not None:
                raise ValueError("user-guided discussion must not set max_rounds")
            return self
        if self.max_rounds is None:
            self.max_rounds = 3
        return self


class VersionedCommand(BaseModel):
    """所有 discussion 写命令共用的幂等身份和乐观版本。"""

    model_config = ConfigDict(extra="forbid")

    command_id: CommandId
    expected_version: int = Field(ge=1)


class ContinueDiscussionRequest(VersionedCommand):
    """在共同公开边界选择继续一轮或一段自动推进。

    Attributes:
        progression_mode: user_guided 只开始下一轮；automatic 在其后继续自动运行。
        additional_rounds: automatic 模式从当前边界起总共再运行的轮数。
    """

    progression_mode: Literal["automatic", "user_guided"] = "user_guided"
    additional_rounds: int | None = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_additional_rounds(self) -> ContinueDiscussionRequest:
        """约束自动模式必须有轮数，逐轮模式不能携带轮数。

        Returns:
            轮数语义明确的继续请求。

        Raises:
            ValueError: 模式与 additional_rounds 不一致。
        """

        if self.progression_mode == "automatic":
            if self.additional_rounds is None:
                raise ValueError("automatic continuation requires additional_rounds")
            return self
        if self.additional_rounds is not None:
            raise ValueError("user-guided continuation must not set additional_rounds")
        return self


class AddDiscussionMessageRequest(VersionedCommand):
    """在两轮之间保存一条给全体或单方的用户原话。

    Attributes:
        body: 用户补充原话。
        target_participant_id: None 表示全体，否则只要求指定 participant 回应。
    """

    body: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)
    ]
    target_participant_id: str | None = None


class StopDiscussionRequest(VersionedCommand):
    """停止研讨并请求收敛 participant 资源。

    Attributes:
        reason: 可选的人类停止原因；不进入 participant prompt。
    """

    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ] = "用户停止研讨"


class MarkDiscussionResultRequest(VersionedCommand):
    """设置一个已公开参与者结果是否进入交接现场。

    Attributes:
        round_number: 已公开结果所属的逻辑轮次。
        participant_id: 创建时冻结的参与者 ID。
        marked: True 加入交接现场，False 取消标记。
    """

    round_number: int = Field(ge=1)
    participant_id: NonEmptyText
    marked: bool = Field(strict=True)


class AnswerParticipantQuestionRequest(BaseModel):
    """回答某个运行中 attempt 明确发出的 AskUserQuestion。

    Attributes:
        participant_id: 发问参与者的稳定 ID。
        attempt_id: 发问所属物理尝试 ID。
        request_id: 实时 elicit_request 携带的请求 ID。
        answers: 问题正文到用户填写答案的对应表。
    """

    model_config = ConfigDict(extra="forbid")

    participant_id: NonEmptyText
    attempt_id: NonEmptyText
    request_id: NonEmptyText
    answers: dict[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)],
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)],
    ] = Field(min_length=1, max_length=20)


class HandoffAgentRequest(BaseModel):
    """创建普通 Agent 接受研讨现场所需的运行条件。

    Attributes:
        runtime: 普通 Agent 使用 Claude Code 还是 Codex。
        connection_id: 已验证的 Trowel 连接 ID。
        workdir: 新 Agent 独立选择的工作目录。
        model: 连接允许的模型 ID。
        effort: 连接允许的思考强度；runtime 不使用时为 None。
        permission_mode: Claude Code 权限模式。
        permission_preset: Codex 权限预设。
        memory_enabled: 是否注入 Trowel Memory。
        profile_enabled: 是否注入已确认的用户画像。
        self_enabled: 是否注入 Trowel 持续主体说明。
    """

    model_config = ConfigDict(extra="forbid")

    runtime: Literal["claude_code", "codex"]
    connection_id: NonEmptyText
    workdir: NonEmptyText
    model: NonEmptyText
    effort: str | None = None
    permission_mode: str | None = None
    permission_preset: CodexPermissionPreset | None = None
    memory_enabled: bool = Field(default=True, strict=True)
    profile_enabled: bool = Field(default=True, strict=True)
    self_enabled: bool = Field(default=True, strict=True)


class CreateDiscussionHandoffRequest(VersionedCommand):
    """携带用户首条指令、普通 Agent 条件和 discussion 乐观并发身份。

    Attributes:
        instruction: 用户在看过交接现场后给新 Agent 的首条原话。
        agent: 与普通 Agent 新会话一致的运行条件。
    """

    instruction: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)
    ]
    agent: HandoffAgentRequest
