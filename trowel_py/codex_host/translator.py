"""将 Codex app-server 通知翻译为内部事件。

translator 不保存会话状态；manager 负责 thread 路由和诊断，session 将翻译结果
绑定到 Trowel 会话并分配序号。读取的原生字段只能来自真实录制或上游协议；已映射
通知缺少必填字段即视为协议漂移。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from trowel_py.codex_host.command_mcp_codec import (
    command_actions as _run_command_actions,
    command_completed_item as _run_command_completed_item,
    command_started_item as _run_command_started_item,
    mcp_tool_completed_item as _run_mcp_tool_completed_item,
    mcp_tool_name as _run_mcp_tool_name,
    mcp_tool_started_item as _run_mcp_tool_started_item,
)
from trowel_py.codex_host.errors import ProtocolViolationError
from trowel_py.codex_host.events import (
    CodexEventType,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host.file_change_codec import HUNK_HEADER as _HUNK_HEADER
from trowel_py.codex_host.file_change_codec import (
    file_change_to_change as _run_file_change_to_change,
)
from trowel_py.codex_host.file_change_codec import (
    file_change_write_diff as _run_file_change_write_diff,
)
from trowel_py.codex_host.file_change_codec import (
    full_file_hunk as _run_full_file_hunk,
)
from trowel_py.codex_host.file_change_codec import (
    parse_unified_diff as _run_parse_unified_diff,
)

# 此集合供 manager 在调用 translate 前静默丢弃 method；启用方法还需确定
# global/thread 路由，并接通 adapter 与公开事件词表。
# warning 可没有 threadId，仅移出集合会被 manager 记为 no_thread_id orphan。
# 未知且未列入集合的方法仍进入诊断；移出前必须确认协议并取得可信 fixture。
_IGNORED_METHODS: frozenset[str] = frozenset(
    {
        # start/resume 响应已生成 session_started，后续通知会重复且没有顶层 threadId。
        "thread/started",
        # 尚无可信 fixture 或消费契约。
        "mcpServer/startupStatus/updated",
        "serverRequest/resolved",
        "thread/turns/list",
        "thread/items/list",
        # 已有 handler，但仍受 manager 门控。
        "warning",
        "guardianWarning",
        # shape 尚未验证，也没有 handler。
        "configWarning",
        "deprecationNotice",
    }
)

# 账户级通知没有顶层 threadId；manager 将其广播给全部已注册 session。
# 新方法只有经上游协议确认属于账户作用域后才能加入。
_ACCOUNT_LEVEL_METHODS: frozenset[str] = frozenset(
    {
        "account/rateLimits/updated",
    }
)

_TURN_COMPLETED = "completed"
_TURN_INTERRUPTED = "interrupted"
_TURN_FAILED = "failed"
_TURN_IN_PROGRESS = "inProgress"
_GOAL_STATUSES = frozenset(
    {"active", "paused", "blocked", "usageLimited", "budgetLimited", "complete"}
)

# 未命中 started/completed 分支的 item.type 返回空结果；已映射类型结构不符则
# 报告协议漂移。
_ITEM_COMMAND = "commandExecution"
_ITEM_AGENT_MSG = "agentMessage"
_ITEM_REASONING = "reasoning"
_ITEM_FILE_CHANGE = "fileChange"
_ITEM_MCP_TOOL = "mcpToolCall"
_ITEM_SUBAGENT = "subAgentActivity"
_ITEM_COLLAB_AGENT_TOOL = "collabAgentToolCall"
_ITEM_COMPACT = "contextCompaction"  # 仅 completed 形成边界
_ITEM_REVIEW_ENTERED = "enteredReviewMode"
_ITEM_REVIEW_EXITED = "exitedReviewMode"

_FC_ADD = "add"
_FC_DELETE = "delete"
_FC_UPDATE = "update"

_CMD_IN_PROGRESS = "inProgress"
_CMD_FAILED = "failed"
_CMD_DECLINED = "declined"

# CommandAction 只接受表中 tag，包括协议定义的 unknown；其他 tag 视为协议漂移。
_COMMAND_ACTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "read": ("command", "name", "path"),
    "listFiles": ("command", "path"),
    "search": ("command", "query", "path"),
    "unknown": ("command",),
}


def _require(params: Mapping[str, Any], key: str, method: str) -> Any:
    """读取协议必填字段，缺少键时报告所属 method 的协议漂移。

    这里只检查键是否存在；需要拒绝 ``None`` 或错误类型时由调用方继续校验。
    """

    if key not in params:
        raise ProtocolViolationError(
            f"notification {method!r} missing required field {key!r}",
            payload=dict(params),
        )
    return params[key]


def _as_str(value: Any) -> str:
    """保留字符串原值，其他值直接使用 ``str()`` 转换。

    本函数不校验协议类型，``None`` 也会转换为 ``"None"``。
    """

    return value if isinstance(value, str) else str(value)


def _mcp_tool_name(server: Any, tool: Any) -> str:
    """用非空的 MCP 服务名和工具名生成显示名称；两者均无效时返回 ``"mcp"``。

    本入口保留既有签名，并在调用共享 codec 时传入 translator 当前的依赖。
    """

    return _run_mcp_tool_name(
        server,
        tool,
        isinstance_fn=isinstance,
        str_type=str,
    )


def _command_actions(item: Mapping[str, Any], method: str) -> tuple[dict[str, Any], ...]:
    """校验命令条目的 ``commandActions`` 并转换为公开动作列表。

    ``method`` 仅用于在协议异常中标明通知来源。本入口保留既有签名，并在调用
    共享 codec 时传入 translator 当前的依赖。
    """

    return _run_command_actions(
        item,
        method,
        require_fn=_require,
        isinstance_fn=isinstance,
        list_type=list,
        mapping_type=Mapping,
        str_type=str,
        protocol_violation_type=ProtocolViolationError,
        dict_type=dict,
        action_fields=_COMMAND_ACTION_FIELDS,
        tuple_type=tuple,
    )


def _parse_unified_diff(patch: str) -> tuple[dict[str, Any], ...]:
    """提取 unified diff 中的区块范围和带标记内容行。

    本入口保留既有签名，并使用 translator 当前的 hunk header 调用共享 codec。
    """

    return _run_parse_unified_diff(patch, hunk_header=_HUNK_HEADER)


def _full_file_hunk(text: str, marker: str) -> tuple[dict[str, Any], ...]:
    """将新增或删除文件的完整内容转换为单个变更区块。

    ``marker`` 使用 ``"+"`` 表示新增，使用 ``"-"`` 表示删除。本入口保留既有签名。
    """

    return _run_full_file_hunk(text, marker)


def _file_change_write_diff(kind_type: Any, diff: Any) -> dict[str, Any]:
    """按文件操作类型将完整内容或 unified diff 转换为前端差异对象。

    本入口保留既有签名，并在调用共享 codec 时传入 translator 当前的依赖。
    """

    return _run_file_change_write_diff(
        kind_type,
        diff,
        add_type=_FC_ADD,
        delete_type=_FC_DELETE,
        update_type=_FC_UPDATE,
        full_file_hunk_fn=_full_file_hunk,
        parse_unified_diff_fn=_parse_unified_diff,
        protocol_violation_type=ProtocolViolationError,
    )


def _file_change_to_change(change: Mapping[str, Any], method: str) -> dict[str, Any]:
    """将单个 ``fileChange.changes`` 条目转换为路径、操作类型和差异字段。

    ``method`` 仅用于在协议异常中标明通知来源。本入口保留既有签名，并在调用
    共享 codec 时传入 translator 当前的依赖。
    """

    return _run_file_change_to_change(
        change,
        method,
        add_type=_FC_ADD,
        delete_type=_FC_DELETE,
        update_type=_FC_UPDATE,
        mapping_type=Mapping,
        as_str=_as_str,
        write_diff_fn=_file_change_write_diff,
        protocol_violation_type=ProtocolViolationError,
    )


class CodexTranslator:
    """将单条原生通知无状态地映射为零个或多个内部事件。"""

    def __init__(self) -> None:
        """建立 ``translate()`` 的 method 分发表；manager 另行应用前置门控。"""

        self._dispatch: dict[str, Callable[[Mapping[str, Any]], list[TranslatedItem]]] = {
            "turn/completed": self._on_turn_completed,
            "item/agentMessage/delta": self._on_agent_message_delta,
            "item/reasoning/textDelta": self._on_reasoning_delta,
            "item/reasoning/summaryTextDelta": self._on_reasoning_delta,
            "item/started": self._on_item_started,
            "item/completed": self._on_item_completed,
            "thread/tokenUsage/updated": self._on_token_usage,
            "thread/status/changed": self._on_thread_status,
            "error": self._on_error,
            "account/rateLimits/updated": self._on_rate_limits,
            "thread/goal/updated": self._on_goal_updated,
            "thread/goal/cleared": self._on_goal_cleared,
            "turn/plan/updated": self._on_plan_updated,
            "turn/diff/updated": self._on_turn_diff_updated,
            "warning": self._on_warning,
            "guardianWarning": self._on_warning,
        }

    def translate(self, method: str, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """将一条 app-server 通知映射为内部事件。

        未知 method 或未映射的 ``item.type`` 返回空列表；handler 明确校验的
        必填键、字段类型或枚举值不符合协议时抛出 ``ProtocolViolationError``。

        Args:
            method: app-server 通知的方法名。
            params: 通知携带的参数对象。

        Returns:
            按通知内容生成的内部事件；没有稳定映射时返回空列表。

        Raises:
            ProtocolViolationError: 已校验的通知结构不符合协议。
        """

        handler = self._dispatch.get(method)
        if handler is not None:
            return handler(params)
        return []

    @property
    def ignored_methods(self) -> frozenset[str]:
        """返回 manager 在 translate 前静默丢弃的方法。"""

        return _IGNORED_METHODS

    @property
    def account_level_methods(self) -> frozenset[str]:
        """返回无 threadId、需广播给全部已注册 session 的账户级方法。"""

        return _ACCOUNT_LEVEL_METHODS

    def _on_turn_completed(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """按原生 status 把 ``turn/completed`` 映射为三种终态。

        method 名不代表成功；completed、interrupted、failed 必须分别处理。
        """

        thread_id = _require(params, "threadId", "turn/completed")
        turn = _require(params, "turn", "turn/completed")
        if not isinstance(turn, Mapping):
            raise ProtocolViolationError(
                "turn/completed.turn is not an object",
                payload=dict(params),
            )
        turn_id = turn.get("id")
        status = turn.get("status")
        # completed 通知中的缺失或 inProgress 状态都表示协议漂移。
        if status not in (_TURN_COMPLETED, _TURN_INTERRUPTED, _TURN_FAILED):
            raise ProtocolViolationError(
                f"turn/completed.turn.status has unexpected value {status!r}",
                payload=dict(params),
            )
        payload = immutable_payload(
            turn_id=_as_str(turn_id) if turn_id is not None else None,
            status=_as_str(status),
            error=turn.get("error"),
            duration_ms=turn.get("durationMs"),
            completed_at=turn.get("completedAt"),
        )
        if status == _TURN_COMPLETED:
            item_type = CodexEventType.FINISHED
        elif status == _TURN_INTERRUPTED:
            item_type = CodexEventType.INTERRUPTED
        else:
            item_type = CodexEventType.ERROR
        return [
            TranslatedItem(
                type=item_type,
                thread_id=_as_str(thread_id),
                turn_id=_as_str(turn_id) if turn_id is not None else None,
                payload=payload,
            )
        ]

    def _on_agent_message_delta(
        self, params: Mapping[str, Any]
    ) -> list[TranslatedItem]:
        """保留 item ID 和文本增量，供下游按同一消息连续输出。"""

        return [
            TranslatedItem(
                type=CodexEventType.ASSISTANT_DELTA,
                thread_id=_as_str(_require(params, "threadId", "item/agentMessage/delta")),
                turn_id=_as_str(_require(params, "turnId", "item/agentMessage/delta")),
                item_id=_as_str(_require(params, "itemId", "item/agentMessage/delta")),
                payload=immutable_payload(delta=_as_str(_require(params, "delta", "item/agentMessage/delta"))),
            )
        ]

    def _on_reasoning_delta(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译 reasoning text 与 summary text 的增量。

        ``item/reasoning/textDelta`` 与 ``item/reasoning/summaryTextDelta``
        共用 handler；可选索引写入内部 payload 以区分片段，当前
        AgentEvent adapter 只转发 delta，不透传这些索引。
        """

        item_id = _as_str(_require(params, "itemId", "reasoning delta"))
        payload_fields: dict[str, Any] = {
            "delta": _as_str(_require(params, "delta", "reasoning delta")),
        }
        if "summaryIndex" in params:
            payload_fields["summary_index"] = params["summaryIndex"]
        if "contentIndex" in params:
            payload_fields["content_index"] = params["contentIndex"]
        return [
            TranslatedItem(
                type=CodexEventType.REASONING_DELTA,
                thread_id=_as_str(_require(params, "threadId", "reasoning delta")),
                turn_id=_as_str(_require(params, "turnId", "reasoning delta")),
                item_id=item_id,
                payload=immutable_payload(**payload_fields),
            )
        ]

    def _on_item_started(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译已启用的 item 启动通知。

        ``commandExecution``、``fileChange`` 和 ``mcpToolCall`` 生成
        ``TOOL_STARTED``；``subAgentActivity`` 与 ``collabAgentToolCall``
        生成 ``SUBAGENT_ACTIVITY``。其余类型，包括 ``contextCompaction`` 和
        未知值，返回空结果。
        """

        item = _require(params, "item", "item/started")
        if not isinstance(item, Mapping):
            raise ProtocolViolationError(
                "item/started.item is not an object", payload=dict(params)
            )
        item_type = item.get("type")
        if item_type == _ITEM_COMMAND:
            return [self._command_started_item(params, item)]
        if item_type == _ITEM_FILE_CHANGE:
            return [self._file_change_started_item(params, item)]
        if item_type == _ITEM_MCP_TOOL:
            return [self._mcp_tool_started_item(params, item)]
        if item_type == _ITEM_SUBAGENT:
            return [self._subagent_item(params, item)]
        if item_type == _ITEM_COLLAB_AGENT_TOOL:
            return [self._collab_agent_tool_item(params, item)]
        if item_type == _ITEM_COMPACT:
            # started 不是上下文代际边界，只有 completed 才关闭一代。
            return []
        return []

    def _on_item_completed(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """按 ``item.type`` 翻译完成通知。

        工具、完整助手消息、subagent、compaction 和 review mode 分别映射为对应
        事件；其余类型，包括未知值，返回空结果。
        """

        item = _require(params, "item", "item/completed")
        if not isinstance(item, Mapping):
            raise ProtocolViolationError(
                "item/completed.item is not an object", payload=dict(params)
            )
        item_type = item.get("type")
        if item_type == _ITEM_COMMAND:
            return [self._command_completed_item(params, item)]
        if item_type == _ITEM_AGENT_MSG:
            return [self._agent_message_item(params, item)]
        if item_type == _ITEM_FILE_CHANGE:
            return [self._file_change_completed_item(params, item)]
        if item_type == _ITEM_MCP_TOOL:
            return [self._mcp_tool_completed_item(params, item)]
        if item_type == _ITEM_SUBAGENT:
            return [self._subagent_item(params, item)]
        if item_type == _ITEM_COLLAB_AGENT_TOOL:
            return [self._collab_agent_tool_item(params, item)]
        if item_type == _ITEM_COMPACT:
            # completed 是唯一可信的上下文代际边界。
            return [self._compaction_item(params, item)]
        if item_type in (_ITEM_REVIEW_ENTERED, _ITEM_REVIEW_EXITED):
            return [self._review_mode_item(params, item)]
        # reasoning 已通过 delta 输出；其他类型尚无稳定映射。
        return []

    def _command_started_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """把 ``item/started`` 中的命令执行转换为工具启动条目。

        本方法保留既有签名，并在调用共享 codec 时传入 translator 当前的依赖。
        """

        return _run_command_started_item(
            params,
            item,
            translated_item_type=TranslatedItem,
            event_type=CodexEventType.TOOL_STARTED,
            as_str_fn=_as_str,
            require_fn=_require,
            immutable_payload_fn=immutable_payload,
            item_kind=_ITEM_COMMAND,
            command_actions_fn=_command_actions,
        )

    def _command_completed_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """把 ``item/completed`` 中的命令执行转换为工具完成条目。

        本方法保留既有签名，并在调用共享 codec 时传入 translator 当前的依赖。
        """

        return _run_command_completed_item(
            params,
            item,
            translated_item_type=TranslatedItem,
            event_type=CodexEventType.TOOL_COMPLETED,
            as_str_fn=_as_str,
            require_fn=_require,
            immutable_payload_fn=immutable_payload,
            item_kind=_ITEM_COMMAND,
            command_actions_fn=_command_actions,
        )

    def _mcp_tool_started_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """把 ``item/started`` 中的 MCP 调用转换为工具启动条目。

        本方法保留既有签名，并在调用共享 codec 时传入 translator 当前的依赖。
        """

        return _run_mcp_tool_started_item(
            params,
            item,
            translated_item_type=TranslatedItem,
            event_type=CodexEventType.TOOL_STARTED,
            as_str_fn=_as_str,
            require_fn=_require,
            immutable_payload_fn=immutable_payload,
            item_kind=_ITEM_MCP_TOOL,
            mcp_tool_name_fn=_mcp_tool_name,
        )

    def _mcp_tool_completed_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """把 ``item/completed`` 中的 MCP 调用转换为工具完成条目。

        本方法保留既有签名，并在调用共享 codec 时传入 translator 当前的依赖。
        """

        return _run_mcp_tool_completed_item(
            params,
            item,
            translated_item_type=TranslatedItem,
            event_type=CodexEventType.TOOL_COMPLETED,
            as_str_fn=_as_str,
            require_fn=_require,
            immutable_payload_fn=immutable_payload,
            item_kind=_ITEM_MCP_TOOL,
            mcp_tool_name_fn=_mcp_tool_name,
        )

    def _file_change_started_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """归一化待执行文件及变更类型，供下游构造 apply_patch 启动事件。"""

        return TranslatedItem(
            type=CodexEventType.TOOL_STARTED,
            thread_id=_as_str(_require(params, "threadId", "item/started")),
            turn_id=_as_str(_require(params, "turnId", "item/started")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=_ITEM_FILE_CHANGE,
                changes=tuple(
                    _file_change_to_change(c, "item/started")
                    for c in _require(item, "changes", "item/started")
                ),
                status=item.get("status"),
                started_at=params.get("startedAtMs"),
            ),
        )

    def _file_change_completed_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """保留原生 status，避免把 declined/failed 补丁显示为成功。"""

        return TranslatedItem(
            type=CodexEventType.TOOL_COMPLETED,
            thread_id=_as_str(_require(params, "threadId", "item/completed")),
            turn_id=_as_str(_require(params, "turnId", "item/completed")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                kind=_ITEM_FILE_CHANGE,
                changes=tuple(
                    _file_change_to_change(c, "item/completed")
                    for c in _require(item, "changes", "item/completed")
                ),
                status=item.get("status"),
                completed_at=params.get("completedAtMs"),
            ),
        )

    def _agent_message_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """保留完整助手文本和 phase；下游 adapter 可丢弃它以避免重复 delta。"""

        return TranslatedItem(
            type=CodexEventType.ASSISTANT_MESSAGE,
            thread_id=_as_str(_require(params, "threadId", "item/completed")),
            turn_id=_as_str(_require(params, "turnId", "item/completed")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                text=item.get("text"),
                phase=item.get("phase"),
            ),
        )

    def _on_token_usage(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """保留 total、last 和上下文窗口组成的稀疏用量快照。"""

        usage = _require(params, "tokenUsage", "thread/tokenUsage/updated")
        if not isinstance(usage, Mapping):
            raise ProtocolViolationError(
                "thread/tokenUsage/updated.tokenUsage is not an object",
                payload=dict(params),
            )
        return [
            TranslatedItem(
                type=CodexEventType.USAGE_UPDATED,
                thread_id=_as_str(_require(params, "threadId", "thread/tokenUsage/updated")),
                turn_id=_as_str(params.get("turnId")) if params.get("turnId") is not None else None,
                payload=immutable_payload(
                    total=usage.get("total"),
                    last=usage.get("last"),
                    model_context_window=usage.get("modelContextWindow"),
                ),
            )
        ]

    def _on_thread_status(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译 thread 状态；activeFlags 保留暂停原因。"""

        status = _require(params, "status", "thread/status/changed")
        if not isinstance(status, Mapping):
            raise ProtocolViolationError(
                "thread/status/changed.status is not an object",
                payload=dict(params),
            )
        return [
            TranslatedItem(
                type=CodexEventType.STATUS,
                thread_id=_as_str(_require(params, "threadId", "thread/status/changed")),
                payload=immutable_payload(
                    status=status.get("type"),
                    active_flags=tuple(status.get("activeFlags") or ()),
                ),
            )
        ]

    def _on_error(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译原生错误并保留 ``willRetry``，但不把通知当作 turn 终态。

        turn 是否结束仍以 ``turn/completed`` 为准。
        """

        error = _require(params, "error", "error")
        if not isinstance(error, Mapping):
            raise ProtocolViolationError(
                "error.error is not an object", payload=dict(params)
            )
        return [
            TranslatedItem(
                type=CodexEventType.ERROR,
                thread_id=_as_str(_require(params, "threadId", "error")),
                turn_id=_as_str(params.get("turnId")) if params.get("turnId") is not None else None,
                payload=immutable_payload(
                    kind="native_error",
                    error_type=error.get("type"),
                    message=error.get("message"),
                    will_retry=bool(params.get("willRetry", False)),
                ),
            )
        ]

    def _on_rate_limits(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译无 threadId 的账户级限额快照。

        manager 将结果广播给全部已注册 session。稀疏字段用 ``.get`` 保留
        ``None``，不能为缺失值编造默认语义。
        """

        snapshot = _require(params, "rateLimits", "account/rateLimits/updated")
        if not isinstance(snapshot, Mapping):
            raise ProtocolViolationError(
                "account/rateLimits/updated.rateLimits is not an object",
                payload=dict(params),
            )
        return [
            TranslatedItem(
                type=CodexEventType.RATE_LIMIT_UPDATED,
                payload=immutable_payload(
                    limit_id=snapshot.get("limitId"),
                    limit_name=snapshot.get("limitName"),
                    primary=snapshot.get("primary"),
                    secondary=snapshot.get("secondary"),
                    credits=snapshot.get("credits"),
                    individual_limit=snapshot.get("individualLimit"),
                    spend_control_reached=snapshot.get("spendControlReached"),
                    plan_type=snapshot.get("planType"),
                    rate_limit_reached_type=snapshot.get("rateLimitReachedType"),
                ),
            )
        ]

    def _on_goal_updated(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """校验目标状态闭集，并保留预算、用量和时间戳的完整快照。"""

        method = "thread/goal/updated"
        thread_id = _as_str(_require(params, "threadId", method))
        goal = _require(params, "goal", method)
        if not isinstance(goal, Mapping):
            raise ProtocolViolationError(
                f"notification {method!r} goal is not an object",
                payload=dict(params),
            )
        status = _require(goal, "status", method)
        if status not in _GOAL_STATUSES:
            raise ProtocolViolationError(
                f"notification {method!r} goal has unexpected status {status!r}",
                payload=dict(goal),
            )
        turn_id = params.get("turnId")
        return [
            TranslatedItem(
                type=CodexEventType.GOAL_UPDATED,
                thread_id=thread_id,
                turn_id=turn_id if isinstance(turn_id, str) else None,
                payload=immutable_payload(
                    objective=_as_str(_require(goal, "objective", method)),
                    status=_as_str(status),
                    token_budget=goal.get("tokenBudget"),
                    tokens_used=_require(goal, "tokensUsed", method),
                    time_used_seconds=_require(goal, "timeUsedSeconds", method),
                    created_at=_require(goal, "createdAt", method),
                    updated_at=_require(goal, "updatedAt", method),
                ),
            )
        ]

    def _on_goal_cleared(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """用无 payload 的专用事件表示 thread 已清除目标。"""

        method = "thread/goal/cleared"
        return [
            TranslatedItem(
                type=CodexEventType.GOAL_CLEARED,
                thread_id=_as_str(_require(params, "threadId", method)),
            )
        ]

    def _on_plan_updated(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译当前 turn 的完整 plan 快照。

        TurnPlanStep 没有 id，且只允许 pending/inProgress/completed；
        turn 中断由 turn 状态表达，不虚构 abandoned 步骤。
        """

        plan = _require(params, "plan", "turn/plan/updated")
        if not isinstance(plan, list):
            raise ProtocolViolationError(
                "turn/plan/updated.plan is not an array", payload=dict(params)
            )
        steps = tuple(self._plan_step(raw, "turn/plan/updated") for raw in plan)
        return [
            TranslatedItem(
                type=CodexEventType.PLAN_UPDATED,
                thread_id=_as_str(_require(params, "threadId", "turn/plan/updated")),
                turn_id=_as_str(_require(params, "turnId", "turn/plan/updated")),
                payload=immutable_payload(
                    explanation=params.get("explanation"),
                    steps=steps,
                ),
            )
        ]

    @staticmethod
    def _plan_step(raw: Any, method: str) -> dict[str, Any]:
        """校验无 id 的 TurnPlanStep；未知 status 视为协议漂移。"""

        if not isinstance(raw, Mapping):
            raise ProtocolViolationError(
                f"notification {method!r} plan step is not an object",
                payload={"raw": raw},
            )
        step = _require(raw, "step", method)
        status = _require(raw, "status", method)
        if status not in ("pending", "inProgress", "completed"):
            raise ProtocolViolationError(
                f"notification {method!r} plan step has unexpected status {status!r}",
                payload=dict(raw),
            )
        return {"step": _as_str(step), "status": _as_str(status)}

    def _subagent_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """翻译已由 Codex 0.144.0 真实录制确认的 activity shape。

        父 thread 事件不含 usage、summary 或逐工具明细；这些信息依赖订阅
        sub-thread，不能在此虚构。
        """

        agent_thread_id = _require(item, "agentThreadId", "subAgentActivity")
        return TranslatedItem(
            type=CodexEventType.SUBAGENT_ACTIVITY,
            thread_id=_as_str(_require(params, "threadId", "item/*")),
            turn_id=_as_str(_require(params, "turnId", "item/*")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(
                source="subagent_activity",
                kind=item.get("kind"),
                agent_thread_id=_as_str(agent_thread_id),
                agent_path=item.get("agentPath"),
            ),
        )

    def _collab_agent_tool_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """保留 0.144.0 collabAgentToolCall 的稀疏原生字段。"""

        receiver_thread_ids = item.get("receiverThreadIds")
        if not isinstance(receiver_thread_ids, list):
            raise ProtocolViolationError(
                "collabAgentToolCall.receiverThreadIds is not an array",
                payload=dict(item),
            )
        return TranslatedItem(
            type=CodexEventType.SUBAGENT_ACTIVITY,
            thread_id=_as_str(_require(params, "threadId", "item/*")),
            turn_id=_as_str(_require(params, "turnId", "item/*")),
            item_id=_as_str(_require(item, "id", "collabAgentToolCall")),
            payload=immutable_payload(
                source="collab_agent_tool_call",
                tool=item.get("tool"),
                status=item.get("status"),
                sender_thread_id=item.get("senderThreadId"),
                receiver_thread_ids=tuple(_as_str(value) for value in receiver_thread_ids),
                prompt=item.get("prompt"),
                model=item.get("model"),
                reasoning_effort=item.get("reasoningEffort"),
                agents_states=item.get("agentsStates"),
            ),
        )

    def _compaction_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """把 completed contextCompaction 翻译为代际边界。

        item 只携带 id，token 数来自独立 usage 通知；started 不形成边界。
        """

        return TranslatedItem(
            type=CodexEventType.COMPACTION,
            thread_id=_as_str(_require(params, "threadId", "item/*")),
            turn_id=_as_str(_require(params, "turnId", "item/*")),
            item_id=_as_str(item.get("id")),
            payload=immutable_payload(),
        )

    def _review_mode_item(
        self, params: Mapping[str, Any], item: Mapping[str, Any]
    ) -> TranslatedItem:
        """要求 review 为字符串，并将 item 类型归一化为 entered 或 exited。"""

        review = _require(item, "review", "review mode item")
        if not isinstance(review, str):
            raise ProtocolViolationError(
                "review mode item.review is not a string", payload=dict(item)
            )
        return TranslatedItem(
            type=CodexEventType.REVIEW_MODE,
            thread_id=_as_str(_require(params, "threadId", "item/*")),
            turn_id=_as_str(_require(params, "turnId", "item/*")),
            item_id=_as_str(_require(item, "id", "review mode item")),
            payload=immutable_payload(
                phase=(
                    "entered"
                    if item.get("type") == _ITEM_REVIEW_ENTERED
                    else "exited"
                ),
                review=review,
            ),
        )

    def _on_turn_diff_updated(
        self, params: Mapping[str, Any]
    ) -> list[TranslatedItem]:
        """保留当前 turn 的完整差异字符串，而非增量补丁。"""

        method = "turn/diff/updated"
        diff = _require(params, "diff", method)
        if not isinstance(diff, str):
            raise ProtocolViolationError(
                "turn/diff/updated.diff is not a string", payload=dict(params)
            )
        return [
            TranslatedItem(
                type=CodexEventType.TURN_DIFF_UPDATED,
                thread_id=_as_str(_require(params, "threadId", method)),
                turn_id=_as_str(_require(params, "turnId", method)),
                payload=immutable_payload(diff=diff),
            )
        ]

    def _on_warning(self, params: Mapping[str, Any]) -> list[TranslatedItem]:
        """翻译已实现但仍被 manager 门控的 warning/guardianWarning。

        ``threadId`` 可缺省，且该通知不表示 turn 终态。configWarning 与
        deprecationNotice 的 shape 不同，仍保持忽略。
        """

        message = _require(params, "message", "warning")
        if not isinstance(message, str):
            # _require 只检查键；拒绝 null 等非字符串，避免显示伪造的 "None"。
            raise ProtocolViolationError(
                "warning.message is not a string", payload=dict(params)
            )
        thread_id_raw = params.get("threadId")
        return [
            TranslatedItem(
                type=CodexEventType.HOST_WARNING,
                thread_id=_as_str(thread_id_raw) if thread_id_raw is not None else None,
                payload=immutable_payload(message=_as_str(message)),
            )
        ]
