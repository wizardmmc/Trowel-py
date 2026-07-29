"""在画像建议对象与 JSON 字段字典之间转换。

允许的维度、状态以及构造和类型适配函数由调用方传入，本模块不持有队列存储
策略。
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from trowel_py.memory.types import Suggestion


def suggestion_from_dict(
    item: dict[str, object],
    *,
    valid_dimensions: Collection[str],
    valid_statuses: Collection[str],
    suggestion_type: type[Suggestion],
    cast_value: Callable[[type[Any], object], Any],
    dimension_type: type[Any],
    status_type: type[Any],
) -> Suggestion:
    """按调用方规则解析一条画像建议记录。

    维度和状态必须属于对应允许集合，ID 缺失或为假值时拒绝。状态缺失时取
    ``pending``。sources 只有列表会逐项字符串化并保留，否则为空元组；body
    的假值变为空字符串，date 仅在缺失时取空字符串，随后两者均字符串化。

    策略版本为布尔值、缺失或无法按 ``int(str(value))`` 转换时取 1；整数
    原样保留。维度与状态在构造前交给 ``cast_value``，其余构造规则由
    ``suggestion_type`` 决定。传入的集合、回调和构造器异常原样传播。

    Args:
        item: 一条 JSON 对象记录。
        valid_dimensions: 允许的维度值集合。
        valid_statuses: 允许的状态值集合。
        suggestion_type: 接收标准建议字段的构造器。
        cast_value: 分别适配维度和状态值的回调。
        dimension_type: 传给 ``cast_value`` 的维度目标类型。
        status_type: 传给 ``cast_value`` 的状态目标类型。

    Returns:
        由 ``suggestion_type`` 构造的建议对象。

    Raises:
        ValueError: ID 缺失或为假值，或维度、状态不在允许集合中。
        TypeError: 允许集合无法处理待检查值，或回调、构造器拒绝参数类型。
    """
    dimension = item.get("dimension")
    status = item.get("status", "pending")
    if dimension not in valid_dimensions:
        raise ValueError(f"unknown dimension {dimension!r} in suggestion queue")
    if status not in valid_statuses:
        raise ValueError(f"unknown status {status!r} in suggestion queue")
    if not item.get("id"):
        raise ValueError("suggestion missing id in queue")

    sources = item.get("sources", [])
    raw_policy_version = item.get("policy_version", 1)
    if isinstance(raw_policy_version, bool):
        policy_version = 1
    elif isinstance(raw_policy_version, int):
        policy_version = raw_policy_version
    else:
        try:
            policy_version = int(str(raw_policy_version))
        except (TypeError, ValueError):
            policy_version = 1

    return suggestion_type(
        id=str(item["id"]),
        dimension=cast_value(dimension_type, dimension),
        body=str(item.get("body") or ""),
        sources=(
            tuple(str(source) for source in sources)
            if isinstance(sources, list)
            else ()
        ),
        date=str(item.get("date", "")),
        status=cast_value(status_type, status),
        policy_version=policy_version,
    )


def suggestion_to_dict(suggestion: Suggestion) -> dict[str, object]:
    """按稳定字段顺序把建议编码为新字典。

    Args:
        suggestion: 要编码的建议对象。

    Returns:
        依次包含 ID、维度、正文、来源、日期、状态和策略版本的字典；来源转换
        为新列表，其余字段原样引用。
    """
    return {
        "id": suggestion.id,
        "dimension": suggestion.dimension,
        "body": suggestion.body,
        "sources": list(suggestion.sources),
        "date": suggestion.date,
        "status": suggestion.status,
        "policy_version": suggestion.policy_version,
    }
