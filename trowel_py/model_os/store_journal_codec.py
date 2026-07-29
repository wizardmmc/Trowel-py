"""Model OS Store 的 journal 行编解码。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def payload_json(
    payload: dict[str, Any],
    *,
    redact_fn: Callable[[Any], Any],
    json_dumps: Callable[..., str],
    sha256_fn: Callable[[bytes], Any],
    str_type: Callable[[Any], str],
) -> tuple[str, str]:
    """脱敏编码 Event payload，并计算编码结果的短内容哈希。

    Args:
        payload: 要写入 events 表的原始 payload。
        redact_fn: 返回 payload 脱敏副本的函数。
        json_dumps: 接受 ``json.dumps`` 兼容参数的 JSON 编码函数。
        sha256_fn: 根据字节串创建 SHA-256 哈希对象的函数。
        str_type: JSON 编码不支持某个值时使用的字符串转换函数。

    Returns:
        脱敏 JSON 文本，以及以 ``sha256:`` 开头的 12 位短内容哈希。
    """

    redacted = redact_fn(payload)
    text = json_dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        default=str_type,
    )
    digest = sha256_fn(text.encode("utf-8")).hexdigest()[:12]
    return text, f"sha256:{digest}"


def dumps(
    value: Any,
    *,
    redact_fn: Callable[[Any], Any],
    json_dumps: Callable[..., str],
    str_type: Callable[[Any], str],
) -> str:
    """脱敏并稳定编码一个 journal 内容字段。

    Args:
        value: 要编码的字段值。
        redact_fn: 返回字段脱敏副本的函数。
        json_dumps: 接受 ``json.dumps`` 兼容参数的 JSON 编码函数。
        str_type: JSON 编码不支持某个值时使用的字符串转换函数。

    Returns:
        保留非 ASCII 字符并按对象键排序的 JSON 文本。
    """

    return json_dumps(
        redact_fn(value),
        ensure_ascii=False,
        sort_keys=True,
        default=str_type,
    )


def event_params(
    event: Any,
    payload_text: str,
    payload_hash: str,
) -> tuple[Any, ...]:
    """按 events 表列顺序生成 Event 写入参数。

    Args:
        event: 提供 events 表各结构字段的 Event 对象。
        payload_text: 已脱敏编码的 payload JSON。
        payload_hash: ``payload_text`` 对应的短内容哈希。

    Returns:
        与 Store 的 events 插入语句 18 个占位符一一对应的参数。
    """

    return (
        event.event_id,
        event.kind,
        event.occurred_at,
        event.source,
        event.provenance.value,
        event.policy_version,
        event.work_item_id,
        event.task_id,
        event.episode_id,
        event.native_session_id,
        event.cause_id,
        event.correlation_id,
        event.outcome,
        payload_text,
        payload_hash,
        event.lease_id,
        event.owner,
        event.fencing_token,
    )


def event_identity(event: Any, payload_hash: str) -> tuple[Any, ...]:
    """从 Event 构造用于核对幂等冲突的语义字段元组。

    Args:
        event: 要提取语义身份的 Event。
        payload_hash: Event 脱敏 payload 的短内容哈希。

    Returns:
        不含 Event ID 和发生时间的语义字段元组。
    """

    # 同一 Event 的重试可以使用新的 occurred_at，因此发生时间不参与身份比较。
    return (
        event.kind,
        event.source,
        event.provenance.value,
        event.policy_version,
        event.work_item_id,
        event.task_id,
        event.episode_id,
        event.native_session_id,
        event.cause_id,
        event.correlation_id,
        event.outcome,
        payload_hash,
        event.lease_id,
        event.owner,
        event.fencing_token,
    )


def event_row_identity(
    row: Any,
    payload_hash: str,
    *,
    int_fn: Callable[[Any], int],
) -> tuple[Any, ...]:
    """从 events 表行还原用于核对幂等冲突的语义字段元组。

    Args:
        row: 包含 Event 持久化字段的 SQLite 行。
        payload_hash: 调用方本次编码的 payload 哈希；当前实现不读取该值，身份
            始终使用行内已持久化的 ``payload_hash``。
        int_fn: 把非空 fencing token 转换为整数的函数。

    Returns:
        与 :func:`event_identity` 字段顺序一致的已持久化语义身份。
    """

    fencing_token = row["fencing_token"]
    return (
        row["kind"],
        row["source"],
        row["provenance"],
        row["policy_version"],
        row["work_item_id"],
        row["task_id"],
        row["episode_id"],
        row["native_session_id"],
        row["cause_id"],
        row["correlation_id"],
        row["outcome"],
        row["payload_hash"],
        row["lease_id"],
        row["owner"],
        int_fn(fencing_token) if fencing_token is not None else None,
    )


def decision_params(
    decision: Any,
    *,
    dumps_fn: Callable[[Any], str],
    redact_fn: Callable[[Any], Any],
) -> tuple[Any, ...]:
    """按 decisions 表列顺序生成 Decision 写入参数。

    Args:
        decision: 提供 decisions 表各字段的 Decision 对象。
        dumps_fn: 脱敏并稳定编码信号、候选项和预算的函数。
        redact_fn: 清理原因码的函数。

    Returns:
        与 decisions 插入语句 15 个占位符一一对应的参数。
    """

    return (
        decision.decision_id,
        decision.kind,
        decision.decided_at,
        decision.work_item_id,
        decision.task_id,
        decision.episode_id,
        decision.cause_id,
        decision.correlation_id,
        decision.policy_version,
        dumps_fn(decision.signals),
        dumps_fn(decision.candidates),
        decision.choice,
        redact_fn(decision.reason),
        (
            dumps_fn(decision.budget_before)
            if decision.budget_before is not None
            else None
        ),
        (
            dumps_fn(decision.budget_after)
            if decision.budget_after is not None
            else None
        ),
    )


def lease_from_row(
    row: Any,
    *,
    lease_type: Callable[..., Any],
    int_fn: Callable[[Any], int],
) -> Any:
    """把 leases 表行转换为调用方指定的 Lease 对象。

    Args:
        row: 包含 Lease 持久化字段的 SQLite 行。
        lease_type: 使用关键字参数创建 Lease 对象的构造函数。
        int_fn: 把持久化 fencing token 转换为整数的函数。

    Returns:
        由 ``lease_type`` 创建的 Lease 对象。
    """

    return lease_type(
        lease_id=row["lease_id"],
        resource_type=row["resource_type"],
        resource_id=row["resource_id"],
        owner=row["owner"],
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
        idempotency_key=row["idempotency_key"],
        fencing_token=int_fn(row["fencing_token"]),
    )


def event_from_row(
    row: Any,
    *,
    event_type: Callable[..., Any],
    provenance_type: Callable[[Any], Any],
    json_loads: Callable[[str], Any],
    int_fn: Callable[[Any], int],
) -> Any:
    """把 events 表行转换为调用方指定的 Event 对象。

    Args:
        row: 包含 Event 持久化字段的 SQLite 行。
        event_type: 使用关键字参数创建 Event 对象的构造函数。
        provenance_type: 把持久化来源值转换为 Event 来源类别的函数。
        json_loads: 解码 payload JSON 的函数。
        int_fn: 把非空 fencing token 转换为整数的函数。

    Returns:
        由 ``event_type`` 创建且 payload 已解码的 Event 对象。
    """

    return event_type(
        event_id=row["event_id"],
        kind=row["kind"],
        occurred_at=row["occurred_at"],
        source=row["source"],
        provenance=provenance_type(row["provenance"]),
        policy_version=row["policy_version"],
        payload=json_loads(row["payload"]),
        work_item_id=row["work_item_id"],
        task_id=row["task_id"],
        episode_id=row["episode_id"],
        native_session_id=row["native_session_id"],
        cause_id=row["cause_id"],
        correlation_id=row["correlation_id"],
        outcome=row["outcome"],
        lease_id=row["lease_id"],
        owner=row["owner"],
        fencing_token=(
            int_fn(row["fencing_token"]) if row["fencing_token"] is not None else None
        ),
    )


def decision_from_row(
    row: Any,
    *,
    decision_type: Callable[..., Any],
    json_loads: Callable[[str], Any],
) -> Any:
    """把 decisions 表行转换为调用方指定的 Decision 对象。

    ``None`` 或空字符串预算按未记录处理；其他真值交给 JSON 解码器，因此
    ``"null"``、``"0"`` 和 ``"false"`` 会分别恢复为 None、0 和 False。

    Args:
        row: 包含 Decision 持久化字段的 SQLite 行。
        decision_type: 使用关键字参数创建 Decision 对象的构造函数。
        json_loads: 解码信号、候选项和非空预算 JSON 的函数。

    Returns:
        由 ``decision_type`` 创建且结构字段已解码的 Decision 对象。
    """

    return decision_type(
        decision_id=row["decision_id"],
        kind=row["kind"],
        decided_at=row["decided_at"],
        signals=json_loads(row["signals"]),
        candidates=json_loads(row["candidates"]),
        choice=row["choice"],
        reason=row["reason"],
        policy_version=row["policy_version"],
        budget_before=(
            json_loads(row["budget_before"]) if row["budget_before"] else None
        ),
        budget_after=(json_loads(row["budget_after"]) if row["budget_after"] else None),
        work_item_id=row["work_item_id"],
        task_id=row["task_id"],
        episode_id=row["episode_id"],
        cause_id=row["cause_id"],
        correlation_id=row["correlation_id"],
    )
