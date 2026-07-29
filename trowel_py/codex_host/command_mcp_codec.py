"""把 Codex 命令执行和 MCP 工具通知转换为统一事件条目。

调用方注入事件类型、校验器和构造器，使 translator 的兼容入口仍能在调用时替换
这些依赖。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def mcp_tool_name(
    server: Any,
    tool: Any,
    *,
    isinstance_fn: Callable[[Any, Any], bool],
    str_type: Any,
) -> str:
    """用非空字符串形式的 MCP 服务名和工具名生成显示名称。

    只有一项符合条件时直接返回该项；两项都不符合时返回 ``"mcp"``。
    """

    parts = [
        str_type(part)
        for part in (server, tool)
        if isinstance_fn(part, str_type) and part
    ]
    return ".".join(parts) if parts else "mcp"


def command_actions(
    item: Mapping[str, Any],
    method: str,
    *,
    require_fn: Callable[[Mapping[str, Any], str, str], Any],
    isinstance_fn: Callable[[Any, Any], bool],
    list_type: Any,
    mapping_type: Any,
    str_type: Any,
    protocol_violation_type: Callable[..., Exception],
    dict_type: Callable[[Any], dict[str, Any]],
    action_fields: Mapping[str, tuple[str, ...]],
    tuple_type: Callable[[Any], tuple[dict[str, Any], ...]],
) -> tuple[dict[str, Any], ...]:
    """校验命令条目的 ``commandActions`` 并转换为公开动作列表。

    ``item`` 是原生命令条目，``method`` 只用于标明协议异常来自哪种通知。
    ``action_fields`` 定义允许的动作类型和公开字段；结构错误或未知类型通过
    ``protocol_violation_type`` 拒绝，允许但缺失的字段保留为 None。
    """

    raw_actions = require_fn(item, "commandActions", method)
    if not isinstance_fn(raw_actions, list_type):
        raise protocol_violation_type(
            f"notification {method!r} commandActions is not an array",
            payload=dict_type(item),
        )
    actions: list[dict[str, Any]] = []
    for raw in raw_actions:
        if not isinstance_fn(raw, mapping_type):
            raise protocol_violation_type(
                f"notification {method!r} commandActions entry is not an object",
                payload=dict_type(item),
            )
        action_type = raw.get("type")
        if not isinstance_fn(action_type, str_type):
            raise protocol_violation_type(
                f"notification {method!r} commandActions has non-string type",
                payload=dict_type(item),
            )
        fields = action_fields.get(action_type)
        if fields is None:
            raise protocol_violation_type(
                f"notification {method!r} commandActions has unexpected type "
                f"{action_type!r}",
                payload=dict_type(item),
            )
        action: dict[str, Any] = {"type": action_type}
        action.update({field: raw.get(field) for field in fields})
        actions.append(action)
    return tuple_type(actions)


def command_started_item(
    params: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    translated_item_type: Callable[..., Any],
    event_type: Any,
    as_str_fn: Callable[[Any], str],
    require_fn: Callable[[Mapping[str, Any], str, str], Any],
    immutable_payload_fn: Callable[..., Any],
    item_kind: Any,
    command_actions_fn: Callable[
        [Mapping[str, Any], str],
        tuple[dict[str, Any], ...],
    ],
) -> Any:
    """把 ``item/started`` 中的命令执行条目转为统一的工具启动条目。

    ``params`` 是通知参数，``item`` 是其中的 ``commandExecution`` 条目。
    ``threadId``、``turnId`` 或 ``commandActions`` 缺失时由注入的校验器拒绝。
    """

    return translated_item_type(
        type=event_type,
        thread_id=as_str_fn(require_fn(params, "threadId", "item/started")),
        turn_id=as_str_fn(require_fn(params, "turnId", "item/started")),
        item_id=as_str_fn(item.get("id")),
        payload=immutable_payload_fn(
            kind=item_kind,
            command=item.get("command"),
            cwd=item.get("cwd"),
            source=item.get("source"),
            command_actions=command_actions_fn(item, "item/started"),
            started_at=params.get("startedAtMs"),
        ),
    )


def command_completed_item(
    params: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    translated_item_type: Callable[..., Any],
    event_type: Any,
    as_str_fn: Callable[[Any], str],
    require_fn: Callable[[Mapping[str, Any], str, str], Any],
    immutable_payload_fn: Callable[..., Any],
    item_kind: Any,
    command_actions_fn: Callable[
        [Mapping[str, Any], str],
        tuple[dict[str, Any], ...],
    ],
) -> Any:
    """把 ``item/completed`` 中的命令执行条目转为统一的工具完成条目。

    ``params`` 是通知参数，``item`` 是其中的 ``commandExecution`` 条目。
    ``threadId``、``turnId`` 或 ``commandActions`` 缺失时由注入的校验器拒绝。
    """

    return translated_item_type(
        type=event_type,
        thread_id=as_str_fn(require_fn(params, "threadId", "item/completed")),
        turn_id=as_str_fn(require_fn(params, "turnId", "item/completed")),
        item_id=as_str_fn(item.get("id")),
        payload=immutable_payload_fn(
            kind=item_kind,
            command=item.get("command"),
            cwd=item.get("cwd"),
            source=item.get("source"),
            command_actions=command_actions_fn(item, "item/completed"),
            status=item.get("status"),
            exit_code=item.get("exitCode"),
            output=item.get("aggregatedOutput"),
            duration_ms=item.get("durationMs"),
            completed_at=params.get("completedAtMs"),
        ),
    )


def mcp_tool_started_item(
    params: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    translated_item_type: Callable[..., Any],
    event_type: Any,
    as_str_fn: Callable[[Any], str],
    require_fn: Callable[[Mapping[str, Any], str, str], Any],
    immutable_payload_fn: Callable[..., Any],
    item_kind: Any,
    mcp_tool_name_fn: Callable[[Any, Any], str],
) -> Any:
    """把 ``item/started`` 中的 MCP 调用转为统一的工具启动条目。

    ``params`` 是通知参数，``item`` 是其中的 ``mcpToolCall`` 条目。
    ``threadId``、``turnId``、``server``、``tool``、``id`` 或 ``status`` 缺失时
    由注入的校验器拒绝。
    """

    # 先读取服务名和工具名，使多个必填字段缺失时的首个错误保持稳定。
    server = require_fn(item, "server", "item/started(mcpToolCall)")
    tool = require_fn(item, "tool", "item/started(mcpToolCall)")
    return translated_item_type(
        type=event_type,
        thread_id=as_str_fn(require_fn(params, "threadId", "item/started")),
        turn_id=as_str_fn(require_fn(params, "turnId", "item/started")),
        item_id=as_str_fn(require_fn(item, "id", "item/started(mcpToolCall)")),
        payload=immutable_payload_fn(
            kind=item_kind,
            server=server,
            tool=tool,
            tool_name=mcp_tool_name_fn(server, tool),
            arguments=item.get("arguments"),
            status=require_fn(
                item,
                "status",
                "item/started(mcpToolCall)",
            ),
            app_context=item.get("appContext"),
            mcp_app_resource_uri=item.get("mcpAppResourceUri"),
            plugin_id=item.get("pluginId"),
            started_at=params.get("startedAtMs"),
        ),
    )


def mcp_tool_completed_item(
    params: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    translated_item_type: Callable[..., Any],
    event_type: Any,
    as_str_fn: Callable[[Any], str],
    require_fn: Callable[[Mapping[str, Any], str, str], Any],
    immutable_payload_fn: Callable[..., Any],
    item_kind: Any,
    mcp_tool_name_fn: Callable[[Any, Any], str],
) -> Any:
    """把 ``item/completed`` 中的 MCP 调用转为统一的工具完成条目。

    ``params`` 是通知参数，``item`` 是其中的 ``mcpToolCall`` 条目。
    ``threadId``、``turnId``、``server``、``tool``、``id`` 或 ``status`` 缺失时
    由注入的校验器拒绝。
    """

    # 先读取服务名和工具名，使多个必填字段缺失时的首个错误保持稳定。
    server = require_fn(item, "server", "item/completed(mcpToolCall)")
    tool = require_fn(item, "tool", "item/completed(mcpToolCall)")
    return translated_item_type(
        type=event_type,
        thread_id=as_str_fn(require_fn(params, "threadId", "item/completed")),
        turn_id=as_str_fn(require_fn(params, "turnId", "item/completed")),
        item_id=as_str_fn(require_fn(item, "id", "item/completed(mcpToolCall)")),
        payload=immutable_payload_fn(
            kind=item_kind,
            server=server,
            tool=tool,
            tool_name=mcp_tool_name_fn(server, tool),
            arguments=item.get("arguments"),
            status=require_fn(
                item,
                "status",
                "item/completed(mcpToolCall)",
            ),
            result=item.get("result"),
            error=item.get("error"),
            app_context=item.get("appContext"),
            mcp_app_resource_uri=item.get("mcpAppResourceUri"),
            plugin_id=item.get("pluginId"),
            duration_ms=item.get("durationMs"),
            completed_at=params.get("completedAtMs"),
        ),
    )
