"""Memory store 的冻结值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# NoteId 是可读文件 stem；跨重命名身份与纠错链使用 Note.memory_id。
NoteId = str

EntryType = Literal["core", "note", "diary", "dictionary"]

Verification = Literal["verified", "inferred-untested", "event-data-supported"]
DiaryLayer = Literal["day", "week", "month"]
DictionaryLayer = Literal["L0", "L1"]
Scope = Literal["high-risk", "low-risk"]
# seed 仅用于引导；候选项经人工 approve 以 trial 入 core，再人工 activate。
CoreStatus = Literal["seed", "trial", "active", "retired"]
NoteKind = Literal["fact", "gotcha", "procedure", "preference", "hypothesis"]
NoteStatus = Literal["active", "contradicted", "superseded", "retired"]
# 表示最后一次写入路径的性质，不是逐字段来源。
ProfileSource = Literal["user-edit", "ai-calibration"]
ProfileDimension = Literal["ability", "methodology", "expression", "goal", "other"]
SuggestionStatus = Literal["pending", "accepted", "discarded"]


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoreItem:
    id: str
    imperative: str
    # high-risk 必须走完整检索链；low-risk 才允许快速假设。
    scope: Scope = "high-risk"
    status: CoreStatus = "seed"
    source: str = ""


@dataclass(frozen=True)
class Core:
    items: tuple[CoreItem, ...]


@dataclass(frozen=True)
class Profile:
    """用户维护的画像；AI 只能通过建议队列提案，不能直接写入。"""

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    updated: str = ""
    # 读取时保留原始值；写入时由调用参数覆盖，并按 ProfileSource 校验。
    source: str = "user-edit"


@dataclass(frozen=True)
class Suggestion:
    """AI 画像建议；处理后的记录仍留在队列中供审计。"""

    id: str
    dimension: ProfileDimension
    body: str
    sources: tuple[str, ...] = ()
    date: str = ""
    status: SuggestionStatus = "pending"
    # 旧记录缺少该字段时按 v1 读取，但不原地回写。
    policy_version: int = 1


@dataclass(frozen=True)
class Note:
    """二层可复用知识；memory_id 不随标题或文件 stem 变化。"""

    type: Literal["note"]
    title: str
    tags: tuple[str, ...] = ()
    kind: NoteKind = "fact"
    summary: str = ""
    created: str = ""
    updated: str = ""
    verification: Verification = "inferred-untested"
    verification_reason: str = ""
    pain: int = 0
    pain_reason: str = ""
    conflicts_with: tuple[str, ...] = ()
    memory_id: str = ""
    # status 是唯一生命周期轴；retired/confidence 不作为独立存储字段。
    status: NoteStatus = "active"
    supersedes: tuple[str, ...] = ()
    superseded_by: str = ""
    valid_from: str = ""
    last_verified_at: str = ""
    # refs 记录读取事件；read_sessions/helpful_refs/harmful_refs
    # 是由日志与判定结果重建的用户会话级缓存。
    refs: int = 0
    read_sessions: int = 0
    helpful_refs: int = 0
    harmful_refs: int = 0
    last_ref: str = ""
    trigger: str = ""
    do_not_use_when: str = ""
    sources: tuple[str, ...] = ()
    source_sessions: tuple[str, ...] = ()
    content_hash: str = ""
    body: str = ""


@dataclass(frozen=True)
class Diary:
    type: Literal["diary"]
    date: str
    layer: DiaryLayer = "day"
    period: str = ""
    promoted_knowledge: tuple[str, ...] = ()
    body: str = ""


@dataclass(frozen=True)
class DictionaryEntry:
    type: Literal["dictionary"]
    layer: DictionaryLayer
    domain: str = ""


@dataclass(frozen=True)
class PersistContext:
    """提炼落盘的来源上下文；persist 只能使用这里的来源，不能自行推断。"""

    # segment_id 是同一 episode 内原位 upsert 的稳定键。
    segment_id: str
    cc_session_id: str
    workdir: str
    registered_at: str
    review_date: str
    source_jsonl: str
    # offset 是 source_jsonl 的字节偏移；end=None 表示 EOF。
    source_start_offset: int = 0
    source_end_offset: int | None = None
    # activity_dates 是当前片段事件的真实日历日，避免恢复时按 review_date 重归档；
    # 空值表示没有可归属日期。
    activity_dates: tuple[str, ...] = ()
    date_basis: str = ""
    processed_date: str = ""
