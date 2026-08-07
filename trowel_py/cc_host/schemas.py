"""CC Host 请求模型与前端事件契约。

CC 原始 stream-json 事件离开服务端前统一转换为 Trowel 事件；前端不直接消费
原始 CC 事件。每种事件用字面量 `type` 作为 discriminator。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

PublicSessionKind = Literal["user", "delegate", "probe", "discussion"]

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
    session_kind: PublicSessionKind | SkipJsonSchema[Literal["background"]] = "user"
    agent_mcp_enabled: bool = Field(default=True, strict=True)
    delegation_depth: int = Field(default=0, ge=0, le=1)


class SendMessageRequest(BaseModel):
    """向 CC 会话发送一条非空消息。

    Attributes:
        text: 作为本轮用户输入发送给 CC 的文字。
    """

    text: str = Field(min_length=1)


class AnswerElicitRequest(BaseModel):
    """提交对待处理提问或 plan mode 确认的回答，或取消该请求。

    Attributes:
        answers: 按问题文本索引的答案；默认为空映射。AskUserQuestion 允许后写入
            `control_response.updatedInput.answers`，plan mode 确认只把它作为批准信号。
        cancel: 是否取消请求；默认为 `False`。为 `True` 时忽略 `answers`，并发送
            deny `control_response`。
    """

    answers: dict[str, str] = Field(default_factory=dict)
    cancel: bool = False


class RevertRequest(BaseModel):
    """恢复到指定轮次开始前，并丢弃该轮及后续内容。

    Attributes:
        turn_id: 要恢复的轮次所对应的 checkpoint ID。
    """

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
    """所有 CC 前端事件共享的基类。

    Attributes:
        type: 前端用于区分事件的 discriminator。
    """

    type: str


class SessionStartedEvent(_Event):
    """每个 CC 进程在 system/init 后发布一次。

    init roster 只有名称，没有描述；前端从 `/cc/slash-items` 单独获取描述。空列表
    默认值让旧版 CC 或最小录制缺字段时仍可统一归并。

    Attributes:
        type: 固定为 `session_started`。
        model: CC `system/init` 报告的当前模型；缺失时为空字符串。
        cwd: CC 进程报告的工作目录；缺失时为空字符串。
        cc_session_id: CC 分配的原生会话 ID；缺失时为空字符串。
        tools: CC 报告的可用工具名；缺失时为空列表。
        slash_commands: CC 报告的 slash command 名；缺失时为空列表。
        skills: CC 报告的 skill 名；缺失时为空列表。
        agents: CC 报告的 Agent 名；缺失时为空列表。
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

    Attributes:
        type: 固定为 `turn_start`。
        turn_id: 本轮对应的 checkpoint ID。
        revertible: 本轮是否可恢复。
    """

    type: Literal["turn_start"] = "turn_start"
    turn_id: str
    revertible: bool


class UserEvent(_Event):
    """仅供 history replay 使用的用户消息。

    实时路径由前端乐观追加用户消息，不发布此事件；回放路径补发该事件，使 live 与
    history 继续复用同一 reducer。

    Attributes:
        type: 固定为 `user`。
        text: 回放的用户输入。
        duration_seconds: 本轮近似耗时，无法从历史时间戳推导时为 `None`。
    """

    type: Literal["user"] = "user"
    text: str
    # CC jsonl 可能没有 result；回放用用户条目到末个 assistant 的时间差近似耗时。
    # 时间戳不可用时保持 None，前端不显示耗时。
    duration_seconds: int | None = None


class TextEvent(_Event):
    """CC 助手输出的一段可见文本。

    Attributes:
        type: 固定为 `text`。
        text: 要追加到助手消息的文本。
    """

    type: Literal["text"] = "text"
    text: str


class ThinkingEvent(_Event):
    """CC 助手输出的一段思考内容。

    Attributes:
        type: 固定为 `thinking`。
        text: 要追加到思考区域的文本。
        thinking_duration_seconds: 回放时推导的近似思考时长；无法推导时为
            `None`。
    """

    type: Literal["thinking"] = "thinking"
    text: str
    # history jsonl 没有 thinking_tokens heartbeat，只能用相邻条目时间差近似；
    # 无前序时间戳时保持 None，前端只显示“思考”。
    thinking_duration_seconds: int | None = None


class DiffHunk(BaseModel):
    """与 jsdiff StructuredPatchHunk 对齐的 wire hunk。

    `lines` 保留 `' ctx'`、`'+add'`、`'-rm'` 的首字符标记。

    Attributes:
        oldStart: hunk 在修改前文件中的起始行。
        oldLines: hunk 覆盖的修改前行数。
        newStart: hunk 在修改后文件中的起始行。
        newLines: hunk 覆盖的修改后行数。
        lines: 包含上下文、新增和删除标记的 patch 行。
    """

    oldStart: int
    oldLines: int
    newStart: int
    newLines: int
    lines: tuple[str, ...]


class WriteDiff(BaseModel):
    """CC 执行写工具后生成的 diff。

    前端按 `type` 选择渲染方式；`update` 携带真实 patch，其他类型可使用空
    hunks。

    Attributes:
        type: 文件操作的渲染类型。
        hunks: 按真实文件行号记录的 patch 分块。
    """

    type: Literal["create", "update", "delete"]
    hunks: tuple[DiffHunk, ...]


class ToolCallEvent(_Event):
    """content block 结束后发布的完整 tool_use。

    子代理工具通过 CC envelope 的 `parent_tool_use_id` 指向创建它的 Agent
    tool_call；顶层工具为 None。

    Attributes:
        type: 固定为 `tool_call`。
        tool_use_id: CC 分配的工具调用 ID。
        tool_name: CC 请求调用的工具名。
        input: 传给工具的原始参数对象。
        parent_tool_use_id: 创建子代理的 Agent 工具调用 ID；顶层工具为
            `None`。
    """

    type: Literal["tool_call"] = "tool_call"
    tool_use_id: str
    tool_name: str
    input: dict[str, Any]
    parent_tool_use_id: str | None = None


class ToolProgressEvent(_Event):
    """CC 工具仍在执行时的耗时进度。

    Attributes:
        type: 固定为 `tool_progress`。
        tool_use_id: 对应工具调用的 ID。
        tool_name: 正在执行的工具名。
        elapsed_time_seconds: CC 报告的已执行秒数；缺失时为 0.0。
    """

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
    """CC 请求失败后正在等待下一次重试。

    Attributes:
        type: 固定为 `retrying`。
        attempt: 上游单次请求中的当前重试次数；缺失或无法转换为整数时为 0。
        max_retries: 上游报告的最大重试次数；未报告时为 `None`。
        error_status: 上游失败的 HTTP 状态码；未报告时为 `None`。
        error: 上游报告的错误文本；未报告时为 `None`。
        retry_delay_ms: 下次重试前的等待毫秒数；未报告时为 `None`。
    """

    type: Literal["retrying"] = "retrying"
    attempt: int
    max_retries: int | None = None
    error_status: int | None = None
    error: str | None = None
    retry_delay_ms: int | None = None


class HookEvent(_Event):
    """CC hook 的启动或返回结果。

    Attributes:
        type: 固定为 `hook`。
        hook_name: CC 报告的 hook 名。
        outcome: hook 返回的结果；启动时或上游未提供时为 `None`。
    """

    type: Literal["hook"] = "hook"
    hook_name: str
    outcome: str | None = None


class StatusEvent(_Event):
    """CC 当前所处的运行阶段。

    Attributes:
        type: 固定为 `status`。
        stage: CC 报告的阶段；上游缺失时为 `unknown`。
    """

    type: Literal["status"] = "status"
    stage: str


class ModelChangedEvent(_Event):
    """模型或 effort 切换后立即同步界面，不等待下一次 send 惰性重启 CC。

    None 表示沿用 CC settings.json，不传 `--model` 或 `--effort`。

    Attributes:
        type: 固定为 `model_changed`。
        model: 后续轮次使用的模型；`None` 表示由 CC settings 决定。
        effort: 后续轮次使用的思考强度；`None` 表示由 CC settings 决定。
    """

    type: Literal["model_changed"] = "model_changed"
    model: str | None = None
    effort: str | None = None


class CompactBoundaryEvent(_Event):
    """CC 完成一次上下文 compact。

    `trigger` 原样来自 `compactMetadata.trigger`，用于区分阈值触发的 `auto` 与用户
    执行 `/compact` 产生的 `manual`。

    Attributes:
        type: 固定为 `compact_boundary`。
        trigger: CC 报告的 compact 触发方式；未报告有效字符串时为 `None`。
    """

    type: Literal["compact_boundary"] = "compact_boundary"
    trigger: str | None = None


class ContextUsageEvent(_Event):
    """单条 assistant message 的原始 token usage。

    必须在 envelope 拆成 text/thinking/tool_use 前发布，否则会丢失 `message.usage`、
    `message.model` 与 `message.id`。usage mapping 保持原样，由 context calculator
    解释。

    Attributes:
        type: 固定为 `context_usage`。
        message_id: CC assistant message ID；上游未提供字符串时为 `None`。
        model: 产生该 assistant message 的模型；上游未提供字符串时为
            `None`。
        usage: CC `message.usage` 的原始映射；默认为空映射。
    """

    type: Literal["context_usage"] = "context_usage"
    message_id: str | None = None
    model: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class LocalCommandEvent(_Event):
    """由 Trowel 本地命令生成的文本结果。

    Attributes:
        type: 固定为 `local_command`。
        content: 本地命令的可展示输出。
    """

    type: Literal["local_command"] = "local_command"
    content: str


class FinishedEvent(_Event):
    """CC 一轮正常结束时的用量和费用汇总。

    Attributes:
        type: 固定为 `finished`。
        usage: CC result 报告的原始用量映射；缺失或为空时为空映射。
        total_cost_usd: CC result 报告的美元费用；缺失时为 0.0。
        num_turns: CC result 报告的轮次数；缺失时为 0。
    """

    type: Literal["finished"] = "finished"
    usage: dict[str, Any]
    total_cost_usd: float
    num_turns: int


class SessionExitedEvent(_Event):
    """CC 子进程退出后发布；正常 turn 中必须排在 `FinishedEvent` 之后。

    Attributes:
        type: 固定为 `session_exited`。
        returncode: CC 子进程的退出码。
    """

    type: Literal["session_exited"] = "session_exited"
    returncode: int


class ErrorEvent(_Event):
    """CC 运行或 API 调用失败时的错误信息。

    Attributes:
        type: 固定为 `error`。
        subclass: CC result subtype 或 Trowel 生成的错误分类。
        errors: 要展示或记录的错误文本；默认为空列表。
        api_error_status: 上游 API 状态码；不适用或未报告时为 `None`。
    """

    type: Literal["error"] = "error"
    subclass: str
    errors: list[str] = Field(default_factory=list)
    api_error_status: int | None = None


class InterruptedEvent(_Event):
    """当前 CC 轮次已被用户中断。

    Attributes:
        type: 固定为 `interrupted`。
    """

    type: Literal["interrupted"] = "interrupted"


class StalledWarningEvent(_Event):
    """CC 长时间静默时发布的非终态警告。

    GLM 非流式 backend 的首事件可能很晚，mild/severe 只提示而不杀进程；达到
    `StalledDetector.threshold_kill` 后才发布 ErrorEvent。

    Attributes:
        type: 固定为 `stalled_warning`。
        severity: 静默时长对应的警告级别。
        elapsed_s: 从上一次活动开始计算的静默秒数。
    """

    type: Literal["stalled_warning"] = "stalled_warning"
    severity: Literal["mild", "severe"]
    elapsed_s: float


class ThinkingProgressEvent(_Event):
    """携带累计 token 估算的 thinking heartbeat。

    GLM backend 的 thinking 内容可能只在后续 assistant envelope 到达；heartbeat
    是期间唯一活动信号。秒数和展示文案由前端计算。

    Attributes:
        type: 固定为 `thinking_progress`。
        estimated_tokens: CC heartbeat 报告的累计思考 token 估算值；缺失时为 0。
    """

    type: Literal["thinking_progress"] = "thinking_progress"
    estimated_tokens: int


class SubagentProgressEvent(_Event):
    """Agent tool 创建的子代理进度。

    task_started/task_progress/task_notification 用 `tool_use_id` 归属到对应 Agent
    ToolItem。task_updated 没有该身份且状态与 notification 重复，因此不映射。
    不同阶段只填充各自已知字段，前端按 task 合并。

    Attributes:
        type: 固定为 `subagent_progress`。
        tool_use_id: 创建该子代理的 Agent 工具调用 ID。
        task_id: CC 为子代理任务分配的 ID。
        status: CC 报告的任务状态；前端将 `started` 和 `progress` 之外的值
            视为终态。
        description: 任务说明；当前阶段未报告时为 `None`。
        subagent_type: 子代理类型；当前阶段未报告时为 `None`。
        last_tool_name: 子代理最后使用的工具名；当前阶段未报告时为 `None`。
        usage: CC 报告的子代理用量；当前阶段未报告时为 `None`。
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
    """需要用户回答或确认的 CC control_request。

    translator 处理 AskUserQuestion、EnterPlanMode 与 ExitPlanMode 的
    `can_use_tool` 请求；回答通过带 `updatedInput` 的 allow control_response 写回
    CC stdin，取消则写 deny。

    Attributes:
        type: 固定为 `elicit_request`。
        tool_use_id: 交互工具调用 ID。
        request_id: 回写 `control_response` 时使用的 CC 请求 ID。
        tool_name: 触发交互的 CC 工具名。
        questions: AskUserQuestion 的原始问题，或 plan mode 工具的宿主确认问题。
    """

    type: Literal["elicit_request"] = "elicit_request"
    tool_use_id: str
    request_id: str
    tool_name: str
    # AskUserQuestion 原样沿用上游 questions，plan mode 由宿主生成同一宽松 shape；
    # 不收紧为专用模型，避免与 CC 仍在演进的 question/options 强耦合。
    questions: list[dict[str, Any]]


class WorkflowPhaseInfo(BaseModel):
    """workflow 的阶段信息。

    优先按 `wf_<runId>.json` 顶层非空 `phases` 列表的顺序生成；该字段不是非空列表时，
    从 `workflowProgress` 的 `workflow_phase` 事件按 `index` 排序恢复。

    Attributes:
        title: 阶段的显示标题。
        detail: 阶段的补充说明；上游未提供时为 `None`。
    """

    title: str
    detail: str | None = None


class WorkflowAgentInfo(BaseModel):
    """来自 workflowProgress 中 `workflow_agent` 的代理节点。

    tokens 和 toolCalls 使用 CC 已聚合的计数，lastToolName 使用 CC 报告的最后工具名。
    `state` 是稳定的 Trowel wire 枚举；已知 CC 状态按映射归一化，未知或非字符串状态
    回退为 `running`。

    Attributes:
        agent_id: workflow run 内的 Agent ID。
        label: Agent 的显示名称。
        phase_index: Agent 所属阶段的索引；上游未提供或无法转换为整数时为
            `None`。
        phase_title: Agent 所属阶段的标题；上游未提供时为 `None`。
        model: Agent 使用的模型；上游未提供时为 `None`。
        state: 归一化后的 Agent 运行状态。
        tokens: CC 聚合的 token 用量；上游未提供或无法转换为整数时为 `None`。
        tool_calls: CC 聚合的工具调用数；上游未提供或无法转换为整数时为
            `None`。
        last_tool_name: Agent 最后使用的工具名；上游未提供时为 `None`。
        duration_ms: Agent 运行时长的毫秒数；上游未提供或无法转换为整数时为
            `None`。
        prompt_preview: Agent prompt 的预览文本；上游未提供时为 `None`。
        result_preview: Agent 结果的预览文本；上游未提供时为 `None`。
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

    Attributes:
        type: 固定为 `workflow_tree`。
        run_id: CC 分配的 workflow run ID，用于独立替换同一 run 的快照。
        task_id: workflow 关联的 task ID；上游未提供时为 `None`。
        name: workflow 的显示名称。
        args: workflow 参数的可展示文本；上游未提供时为 `None`。
        status: 归一化后的 workflow 运行状态；值不受支持时回退为 `running`。
        agent_count: CC 报告的 Agent 总数；无有效值时为 0。
        done_count: `agents` 中状态为 `done` 的 Agent 数。
        total_tokens: CC 报告的 workflow token 总量；未提供或无法转换为整数时为
            `None`。
        total_tool_calls: CC 报告的 workflow 工具调用总数；未提供或无法转换为整数时为
            `None`。
        duration_ms: workflow 运行时长，单位为毫秒；未提供或无法转换为整数时为
            `None`。
        phases: 按展示顺序排列的 workflow 阶段；默认为空列表。
        agents: workflow 中的 Agent 节点；默认为空列表。
        error: workflow 失败信息；上游未提供时为 `None`。
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
