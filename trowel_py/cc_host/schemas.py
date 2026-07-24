"""CC Host 请求模型与前端事件契约。

CC 原始 stream-json 事件离开服务端前统一转换为 Trowel 事件；前端不直接消费
原始 CC 事件。每种事件用字面量 `type` 作为 discriminator。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 请求模型


class CreateSessionRequest(BaseModel):
    """创建或恢复 CC 会话，并设置本次运行使用的模型、权限与注入开关。"""

    workdir: str = Field(min_length=1)
    resume_from: str | None = None
    permission_mode: str = "bypassPermissions"
    model: str | None = None
    effort: str | None = None
    # 注入开关只接受 JSON boolean；字符串和数字不能按 truthiness 强制转换。
    memory_enabled: bool = Field(default=True, strict=True)
    profile_enabled: bool = Field(default=True, strict=True)
    self_enabled: bool = Field(default=True, strict=True)


class SendMessageRequest(BaseModel):
    """向 CC 会话发送一条非空消息。"""

    text: str = Field(min_length=1)


class AnswerElicitRequest(BaseModel):
    """回答待处理的 AskUserQuestion；`cancel` 会写入 deny control_response。"""

    answers: dict[str, str] = Field(default_factory=dict)
    cancel: bool = False


class RevertRequest(BaseModel):
    """恢复到 `turn_id` 对应 turn 之前，并丢弃该 turn 及后续内容。"""

    turn_id: str = Field(min_length=1)


# 前端消费的 Trowel 事件模型

# 每种事件各占一个 discriminator 字符串。
EVENT_TYPES = frozenset(
    {
        "session_started",
        "user",
        "text",
        "thinking",
        "tool_call",
        "tool_progress",
        "tool_result",
        "retrying",
        "hook",
        "status",
        "compact_boundary",
        "local_command",
        "finished",
        "error",
        "interrupted",
        "stalled_warning",
        "thinking_progress",
        "subagent_progress",
        "elicit_request",
        "turn_start",
        "model_changed",
        "workflow_tree",
        # /exit 会产生 session_exited，AgentEvent envelope 必须接受该终态。
        "session_exited",
        # assistant envelope 拆分后会丢失 usage/model/message_id，须先发布用量。
        "context_usage",
    }
)


class _Event(BaseModel):
    type: str


class SessionStartedEvent(_Event):
    """每个 CC 进程在 system/init 后发布一次。

    init roster 只有名称，没有描述；前端从 `/cc/slash-items` 单独获取描述。空列表
    默认值让旧版 CC 或最小录制缺字段时仍可统一归并。
    """

    type: Literal["session_started"] = "session_started"
    model: str
    cwd: str
    cc_session_id: str
    tools: list[str]
    slash_commands: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)


class TurnStartEvent(_Event):
    """实时 turn 的 checkpoint 身份与可恢复性。

    history replay 没有当前进程创建的 checkpoint，因此不发布此事件。非 Git 工作
    目录不可恢复；Git 首轮复用启动 checkpoint，后续 turn 创建新 checkpoint。
    """

    type: Literal["turn_start"] = "turn_start"
    turn_id: str
    revertible: bool


class UserEvent(_Event):
    """仅供 history replay 使用的用户消息。

    实时路径由前端乐观追加用户消息，不发布此事件；回放路径补发该事件，使 live 与
    history 继续复用同一 reducer。
    """

    type: Literal["user"] = "user"
    text: str
    # CC jsonl 可能没有 result；回放用用户条目到末个 assistant 的时间差近似耗时。
    # 时间戳不可用时保持 None，前端不显示耗时。
    duration_seconds: int | None = None


class TextEvent(_Event):
    type: Literal["text"] = "text"
    text: str


class ThinkingEvent(_Event):
    type: Literal["thinking"] = "thinking"
    text: str
    # history jsonl 没有 thinking_tokens heartbeat，只能用相邻条目时间差近似；
    # 无前序时间戳时保持 None，前端只显示“思考”。
    thinking_duration_seconds: int | None = None


class DiffHunk(BaseModel):
    """与 jsdiff StructuredPatchHunk 对齐的 wire hunk。

    `lines` 保留 `' ctx'`、`'+add'`、`'-rm'` 的首字符标记。
    """

    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: tuple[str, ...]


class WriteDiff(BaseModel):
    """CC 执行写工具后生成的 diff。

    `create` 使用空 hunks，`update` 携带真实 patch；前端按 `type` 选择渲染方式。
    """

    type: Literal["create", "update", "delete"]
    hunks: tuple[DiffHunk, ...]


class ToolCallEvent(_Event):
    """content block 结束后发布的完整 tool_use。

    子代理工具通过 CC envelope 的 `parent_tool_use_id` 指向创建它的 Agent
    tool_call；顶层工具为 None。
    """

    type: Literal["tool_call"] = "tool_call"
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    parent_tool_use_id: str | None = None


class ToolProgressEvent(_Event):
    type: Literal["tool_progress"] = "tool_progress"
    tool_use_id: str
    tool_name: str
    elapsed_time_seconds: float


class ToolResultEvent(_Event):
    """CC 在 user message 中返回的 tool_use 结果。

    Edit/MultiEdit/Write 的 `write_diff` 来自 CC 执行时生成的 `structuredPatch`，
    因而保留真实文件行号，并让 live 与 jsonl replay 一致。其他工具、失败或 no-op
    编辑没有该字段，前端回退到 fragment diff。
    """

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    write_diff: WriteDiff | None = None


class RetryingEvent(_Event):
    type: Literal["retrying"] = "retrying"
    attempt: int
    max_retries: int | None = None
    error_status: int | None = None
    error: str | None = None
    retry_delay_ms: int | None = None


class HookEvent(_Event):
    type: Literal["hook"] = "hook"
    hook_name: str
    outcome: str | None = None


class StatusEvent(_Event):
    type: Literal["status"] = "status"
    stage: str


class ModelChangedEvent(_Event):
    """模型或 effort 切换后立即同步界面，不等待下一次 send 惰性重启 CC。

    None 表示沿用 CC settings.json，不传 `--model` 或 `--effort`。
    """

    type: Literal["model_changed"] = "model_changed"
    model: str | None = None
    effort: str | None = None


class CompactBoundaryEvent(_Event):
    """CC 完成一次上下文 compact。

    `trigger` 原样来自 `compactMetadata.trigger`，用于区分阈值触发的 `auto` 与用户
    执行 `/compact` 产生的 `manual`。
    """

    type: Literal["compact_boundary"] = "compact_boundary"
    trigger: str | None = None


class ContextUsageEvent(_Event):
    """单条 assistant message 的原始 token usage。

    必须在 envelope 拆成 text/thinking/tool_use 前发布，否则会丢失 `message.usage`、
    `message.model` 与 `message.id`。usage mapping 保持原样，由 context calculator
    解释。
    """

    type: Literal["context_usage"] = "context_usage"
    message_id: str | None = None
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class LocalCommandEvent(_Event):
    type: Literal["local_command"] = "local_command"
    content: str


class FinishedEvent(_Event):
    type: Literal["finished"] = "finished"
    usage: dict[str, Any]
    total_cost_usd: float
    num_turns: int


class SessionExitedEvent(_Event):
    """CC 子进程退出后发布；正常 turn 中必须排在 FinishedEvent 之后。"""

    type: Literal["session_exited"] = "session_exited"
    returncode: int


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    subclass: str
    errors: list[str] = Field(default_factory=list)
    api_error_status: int | None = None


class InterruptedEvent(_Event):
    type: Literal["interrupted"] = "interrupted"


class StalledWarningEvent(_Event):
    """CC 长时间静默时发布的非终态警告。

    GLM 非流式 backend 的首事件可能很晚，mild/severe 只提示而不杀进程；达到
    `StalledDetector.threshold_kill` 后才发布 ErrorEvent。
    """

    type: Literal["stalled_warning"] = "stalled_warning"
    severity: Literal["mild", "severe"]
    elapsed_s: float


class ThinkingProgressEvent(_Event):
    """携带累计 token 估算的 thinking heartbeat。

    GLM backend 的 thinking 内容可能只在后续 assistant envelope 到达；heartbeat
    是期间唯一活动信号。秒数和展示文案由前端计算。
    """

    type: Literal["thinking_progress"] = "thinking_progress"
    estimated_tokens: int


class SubagentProgressEvent(_Event):
    """Agent tool 创建的子代理进度。

    task_started/task_progress/task_notification 用 `tool_use_id` 归属到对应 Agent
    ToolItem。task_updated 没有该身份且状态与 notification 重复，因此不映射。
    不同阶段只填充各自已知字段，前端按 task 合并。
    """

    type: Literal["subagent_progress"] = "subagent_progress"
    tool_use_id: str
    task_id: str
    # task_notification 的真实录制确认 completed/failed/cancelled；其他值尚未录制。
    # 保持 str 可避免未知 CC 状态让 translator 崩溃；前端把 started/progress 之外
    # 的值视为终态。
    status: str
    description: str | None = None
    subagent_type: str | None = None
    last_tool_name: str | None = None
    usage: dict[str, Any] | None = None


class ElicitationRequestEvent(_Event):
    """AskUserQuestion 的 control_request。

    translator 只处理 `can_use_tool` 且 tool_name 为 AskUserQuestion 的请求；回答通过
    带 `updatedInput` 的 allow control_response 写回 CC stdin，取消则写 deny。
    """

    type: Literal["elicit_request"] = "elicit_request"
    tool_use_id: str
    request_id: str
    # questions 原样来自 control_request.input.questions；宽松 dict 避免与 CC 仍在
    # 演进的 question/options shape 强耦合。
    questions: list[dict[str, Any]]


class WorkflowPhaseInfo(BaseModel):
    """来自 `wf_<runId>.json` 顶层 phases 数组的阶段。

    数组顺序就是展示顺序，上游没有独立 order 字段。
    """

    title: str
    detail: str | None = None


class WorkflowAgentInfo(BaseModel):
    """来自 workflowProgress 中 `workflow_agent` 的代理节点。

    tokens/toolCalls/lastToolName 使用 CC 已聚合的值。`state` 是稳定 Trowel wire
    枚举；watcher 把 CC 的 start/progress/done/error 归一化后再写入。
    """

    agent_id: str
    label: str
    phase_index: int | None = None
    phase_title: str | None = None
    model: str | None = None
    state: Literal["queued", "running", "done", "failed"]
    tokens: int | None = None
    tool_calls: int | None = None
    last_tool_name: str | None = None
    duration_ms: int | None = None
    prompt_preview: str | None = None
    result_preview: str | None = None


class WorkflowTreeEvent(_Event):
    """单个 workflow run 的完整磁盘快照。

    真实逆向确认 CC 不向 stream-json stdout 发布 workflow 进度；`wf_<runId>.json`
    是 CC TUI 同样读取的事实源。CC 每次重写整文件，因此事件采用 replace 语义而非
    patch。live watcher 与 history replay 使用同一 shape；并行 run 用 `run_id`
    独立归并。
    """

    type: Literal["workflow_tree"] = "workflow_tree"
    run_id: str
    task_id: str | None = None
    name: str
    args: str | None = None
    status: Literal["running", "completed", "killed", "failed"]
    agent_count: int
    # 由 agents 中 state=done 的数量计算，用于 done/total 进度。
    done_count: int
    total_tokens: int | None = None
    total_tool_calls: int | None = None
    duration_ms: int | None = None
    phases: list[WorkflowPhaseInfo] = Field(default_factory=list)
    agents: list[WorkflowAgentInfo] = Field(default_factory=list)
    error: str | None = None


# 仅供类型标注的 Trowel 事件联合，不承担运行时校验。
TrowelEvent = (
    SessionStartedEvent
    | TurnStartEvent
    | UserEvent
    | TextEvent
    | ThinkingEvent
    | ToolCallEvent
    | ToolProgressEvent
    | ToolResultEvent
    | RetryingEvent
    | HookEvent
    | StatusEvent
    | CompactBoundaryEvent
    | ContextUsageEvent
    | LocalCommandEvent
    | FinishedEvent
    | SessionExitedEvent
    | ErrorEvent
    | InterruptedEvent
    | StalledWarningEvent
    | ThinkingProgressEvent
    | SubagentProgressEvent
    | ElicitationRequestEvent
    | ModelChangedEvent
    | WorkflowTreeEvent
)
