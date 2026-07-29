"""Tidy 计划的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

OpType = Literal[
    "merge_sources",
    "revise",
    "supersede",
    "contradict",
    "retire",
    "keep",
]


@dataclass(frozen=True)
class TidyOperation:
    """描述一项需要校验后执行的 Note 整理操作。

    Attributes:
        type: 要执行的操作；六种取值分别表示合并来源、修订字段、被新结论
            取代、被新结论证伪、退役或保持不变。
        target: 本次操作处理的 Note 记忆 ID。
        reason: 生成该操作的理由。
        evidence: 支持该操作的证据来源 ID。
        expected_revision: 执行前期望匹配的目标 ``content_hash``；空值时可由计划
            快照中该目标的值补足，两者都没有时不检查哈希是否变化。
        canonical: ``merge_sources`` 操作保留的 Note 记忆 ID；目标 Note 会被
            标为 ``superseded``，其来源会合入该 Note。
        by: ``supersede`` 或 ``contradict`` 操作用来取代或证伪目标的 Note
            记忆 ID。
        new_fields: ``revise`` 操作请求更新的字段；校验和执行阶段只接受白名单
            字段。

    ``frozen=True`` 只禁止字段重新赋值，不会冻结 ``new_fields`` 字典。
    """

    type: OpType
    target: str
    reason: str
    evidence: tuple[str, ...] = ()
    expected_revision: str = ""
    canonical: str = ""
    by: str = ""
    new_fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TidyPlan:
    """由来源快照约束的一组 Tidy 变更。

    Attributes:
        plan_id: 计划及其快照目录使用的标识。
        source_snapshot: Note 记忆 ID 到生成计划时 ``content_hash`` 的映射。
        operations: 按执行顺序排列的变更。
        dictionary_rebuild_required: 计划记录的 Dictionary 重建声明；
            ``apply_plan`` 只把该值写入计划快照，不据此触发重建。
        core_candidates: 计划记录的 Core 候选记忆 ID；``apply_plan`` 只把这些
            ID 写入计划快照，不执行晋升。

    ``frozen=True`` 只禁止字段重新赋值，不会递归冻结 ``source_snapshot`` 或
    操作中的 ``new_fields`` 字典。
    """

    plan_id: str
    source_snapshot: dict[str, str]
    operations: tuple[TidyOperation, ...]
    dictionary_rebuild_required: bool = False
    core_candidates: tuple[str, ...] = ()
