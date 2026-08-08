"""定义研讨领域的不可变事实对象和状态取值。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trowel_py.agent_host.binding import Runtime

ProgressionMode = Literal["automatic", "user_guided"]
DiscussionStatus = Literal[
    "draft",
    "running",
    "waiting_user",
    "completed",
    "stopped",
    "needs_reconcile",
    "deleted",
]
ParticipantStatus = Literal["pending", "ready", "needs_reconcile", "closed"]
RoundKind = Literal["regular", "final"]
RoundStatus = Literal["sealed", "running", "published", "stopped"]
ResultStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "limited",
    "timed_out",
    "interrupted",
    "cancelled",
    "host_lost",
    "needs_reconcile",
]
TargetScope = Literal["all", "participant"]


@dataclass(frozen=True)
class DiscussionParticipant:
    """记录一位创建后不可变的研讨参与者及其原生会话归属。

    Attributes:
        id: 研讨领域内的稳定参与者 ID。
        discussion_id: 所属研讨 ID。
        position: 创建时冻结的展示与发布顺序，从 0 开始。
        name: 用户可见的参与者名称。
        runtime: 执行该参与者的原生运行工具。
        connection_id: 创建时冻结的 Trowel 连接 ID。
        connection_name: 创建时冻结的连接展示名，例如 DeepSeek 或 PRO X20。
        model: 创建时冻结的模型 ID。
        effective_model: 角色别名解析后的真实模型 ID；无法解析时等于 model。
        effort: 创建时冻结的思考强度。
        session_configuration_id: 创建时选择的完整会话配置 ID；没有时为 None。
        permission_mode: Claude Code 创建时冻结的权限模式；Codex 为 None。
        permission_preset: Codex 创建时冻结的权限预设；Claude Code 为 None。
        memory_enabled: 是否向该参与者注入 Trowel Memory。
        profile_enabled: 是否向该参与者注入已确认的用户画像。
        self_enabled: 是否向该参与者注入 Trowel 持续主体说明。
        connection_identity_version: 创建时冻结的连接启动身份版本。
        owner_ref: 用于跨应用重启认领 binding 的持久归属键。
        agent_session_id: 当前 Session Hub 会话 ID；尚未创建时为 None。
        native_session_id: 可跨进程恢复的原生会话或 thread ID。
        status: 会话准备、待对账或关闭状态。
        capability_version: 创建时通过的连接能力版本。
        capability_source: 创建时通过能力门禁的证据说明。
        created_at: 领域记录创建时间。
        updated_at: 领域记录最近更新时间。
    """

    id: str
    discussion_id: str
    position: int
    name: str
    runtime: Runtime
    connection_id: str
    connection_name: str | None
    model: str
    effective_model: str
    effort: str | None
    session_configuration_id: str | None
    permission_mode: str | None
    permission_preset: str | None
    memory_enabled: bool
    profile_enabled: bool
    self_enabled: bool
    connection_identity_version: int | None
    owner_ref: str
    agent_session_id: str | None
    native_session_id: str | None
    status: ParticipantStatus
    capability_version: str | None
    capability_source: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ParticipantResult:
    """记录一轮中某个稳定参与者槽位的公开或待公开状态。

    Attributes:
        participant_id: 对应参与者 ID。
        position: 与创建时 participant 顺序一致的槽位。
        status: 本轮执行结果；只有 succeeded 才有可发布正文。
        current_attempt_id: 当前或最终采用的尝试 ID。
        output_artifact: 成功正文文件的相对路径；未成功时为 None。
        output_sha256: 成功正文的内容指纹。
        output_bytes: 成功正文的 UTF-8 字节数。
        error_code: 稳定失败原因代码。
        error_message: 面向用户的脱敏失败说明。
        usage_json: 运行工具回报的原始用量摘要 JSON；没有时为 None。
        activity_json: 本轮工具调用与子 Agent 活动的去敏统计 JSON。
        started_at: 当前槽位首次开始时间。
        completed_at: 当前槽位进入终态的时间。
    """

    participant_id: str
    position: int
    status: ResultStatus
    current_attempt_id: str | None
    output_artifact: str | None
    output_sha256: str | None
    output_bytes: int | None
    error_code: str | None
    error_message: str | None
    usage_json: str | None
    activity_json: str | None
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class ParticipantAttemptHistoryRequest:
    """保存从原生历史定位一个 discussion attempt 所需的最小事实。

    Attributes:
        id: attempt 稳定 ID。
        discussion_id: 所属研讨 ID。
        round_number: 所属逻辑轮号。
        participant_id: 所属参与者 ID。
        runtime: 原生历史来源。
        agent_session_id: AgentEvent 回放使用的 Trowel 会话 ID。
        native_session_id: Claude session ID 或 Codex thread ID。
        workdir: Claude Code 定位 project JSONL 使用的工作目录。
        input_hash: 精确公共输入的 SHA-256，用于切分 Claude Code user turn。
        input_occurrence: 该参与者原生会话中同一输入第几次被 runtime 接受，从 1 开始。
        root_turn_id: Codex journal 与实时根事件的稳定 turn ID。
        status: attempt 当前或最终状态。
    """

    id: str
    discussion_id: str
    round_number: int
    participant_id: str
    runtime: Runtime
    agent_session_id: str | None
    native_session_id: str | None
    workdir: str
    input_hash: str
    input_occurrence: int
    root_turn_id: str | None
    status: str


@dataclass(frozen=True)
class DiscussionRound:
    """记录一次同步盲答轮及其稳定参与者槽位。

    Attributes:
        id: 轮次稳定 ID。
        discussion_id: 所属研讨 ID。
        number: 从 1 开始的逻辑轮号。
        kind: 普通讨论轮或收尾轮。
        status: 封闭、运行、公开或被停止。
        public_context_hash: 所有参与者共同输入快照的 SHA-256。
        input_artifact: 精确输入快照文件的相对路径。
        input_bytes: 精确输入快照的 UTF-8 字节数。
        publication_artifact: 原子发布清单的相对路径；未发布时为 None。
        publication_sha256: 发布清单的内容指纹。
        publication_bytes: 发布清单的 UTF-8 字节数。
        started_at: 轮次快照冻结时间。
        published_at: 共同公开时间。
        stop_reason: 用户停止或系统中止的原因。
        results: 按 participant position 排序的完整结果槽位。
    """

    id: str
    discussion_id: str
    number: int
    kind: RoundKind
    status: RoundStatus
    public_context_hash: str
    input_artifact: str
    input_bytes: int
    publication_artifact: str | None
    publication_sha256: str | None
    publication_bytes: int | None
    started_at: str
    published_at: str | None
    stop_reason: str | None
    results: tuple[ParticipantResult, ...] = ()


@dataclass(frozen=True)
class UserMessage:
    """记录一条由用户直接写入研讨的顶层原话。

    Attributes:
        id: 消息稳定 ID。
        discussion_id: 所属研讨 ID。
        sequence: 研讨内严格递增顺序。
        after_round_number: 此消息位于哪一轮公开之后，初始议题为 0。
        target_scope: 发给全体还是指定参与者。
        target_participant_id: 单方补充的目标；全体消息为 None。
        body: 用户原话。
        message_artifact: 不可变消息证据文件的相对路径。
        message_sha256: 消息证据文件的内容指纹。
        message_bytes: 消息证据文件的 UTF-8 字节数。
        profile_status: Profile 提炼的独立处理水位。
        profile_source_id: 不与 participant journal 混用的 Profile 来源 ID。
        created_at: 消息保存时间。
    """

    id: str
    discussion_id: str
    sequence: int
    after_round_number: int
    target_scope: TargetScope
    target_participant_id: str | None
    body: str
    message_artifact: str
    message_sha256: str
    message_bytes: int
    profile_status: Literal["pending", "processed", "ignored"]
    profile_source_id: str
    created_at: str


@dataclass(frozen=True)
class Discussion:
    """聚合一场研讨的控制状态、参与者、用户原话和已建立轮次。

    Attributes:
        id: 研讨稳定 ID。
        create_request_id: 创建命令的客户端稳定身份。
        create_request_hash: 创建 payload 的规范化指纹，用于拒绝命令身份挪用。
        topic: 创建研讨时的初始议题原话。
        workdir: participant 工具统一使用的工作目录。
        progression_mode: 当前自动推进或每轮等待用户参与，可在公开边界切换。
        max_rounds: 当前自动批次的绝对停止轮号；用户参与模式为 None。
        status: 研讨当前生命周期状态。
        version: 所有写命令使用的乐观并发版本。
        active_round_number: 当前运行或最近发布的轮号。
        episode_id: 收口后幂等生成的聚合 Episode ID。
        created_at: 研讨创建时间。
        updated_at: 最近可观察状态变化时间。
        completed_at: 正常收尾时间。
        stopped_at: 用户停止时间。
        deleted_at: 软删除时间。
        participants: 创建时冻结并按 position 排序的参与者。
        messages: 按 sequence 排序的顶层用户消息。
        rounds: 按 number 排序的轮次。
    """

    id: str
    create_request_id: str
    create_request_hash: str
    topic: str
    workdir: str
    progression_mode: ProgressionMode
    max_rounds: int | None
    status: DiscussionStatus
    version: int
    active_round_number: int | None
    episode_id: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    stopped_at: str | None
    deleted_at: str | None
    participants: tuple[DiscussionParticipant, ...] = ()
    messages: tuple[UserMessage, ...] = ()
    rounds: tuple[DiscussionRound, ...] = ()
