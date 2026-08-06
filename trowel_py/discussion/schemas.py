"""定义 discussion HTTP 命令的公开 wire shape。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
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
        session_configuration_id: 设置域中已验证的完整会话配置 ID。
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
    session_configuration_id: NonEmptyText


class CreateDiscussionRequest(BaseModel):
    """创建并冻结一场研讨的初始议题、模式和参与者。

    Attributes:
        request_id: 创建请求的客户端稳定身份。
        topic: 初始议题原话，同时登记为第一条 Profile 用户来源。
        workdir: participant 只读工具统一使用的项目目录。
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
