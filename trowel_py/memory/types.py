"""定义 Memory 存储、画像和提炼流程共用的取值范围与冻结数据对象。

数据对象本身不做运行时校验；各读写入口按自身契约执行宽松转换或写前校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from trowel_py.memory.provenance import CompletedSegment, DerivationProvenance

# NoteId 是可读文件 stem；跨重命名身份与纠错链使用 Note.memory_id。
NoteId = str

EntryType = Literal["core", "note", "diary", "dictionary"]

Verification = Literal["verified", "inferred-untested", "event-data-supported"]
DiaryLayer = Literal["day", "week", "month"]
DictionaryLayer = Literal["L0", "L1"]
Scope = Literal["high-risk", "low-risk"]
# seed 只用于初始引导；候选项经人工批准后以 trial 写入 Core，再转为 active。
CoreStatus = Literal["seed", "trial", "active", "retired"]
NoteKind = Literal["fact", "gotcha", "procedure", "preference", "hypothesis"]
NoteStatus = Literal["active", "contradicted", "superseded", "retired"]
# 表示最后一次写入路径的性质，不是逐字段来源。
ProfileSource = Literal["user-edit", "ai-calibration"]
ProfileDimension = Literal["ability", "methodology", "expression", "goal", "other"]
SuggestionStatus = Literal["pending", "accepted", "discarded"]


@dataclass(frozen=True)
class ValidationResult:
    """记录一次 frontmatter 校验的结果。

    Attributes:
        ok: 是否未发现错误。
        errors: 按校验顺序收集的错误文本；通过时为空。
    """

    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoreItem:
    """记录一条可注入模型的 Core 行为要求。

    Attributes:
        id: 条目的稳定标识。
        imperative: 注入模型的行为指令。
        scope: ``high-risk`` 要求完整检索，``low-risk`` 允许快速假设。
        status: 从引导、试用、启用到退场的人工管理状态。
        source: 该要求的来源标识。
    """

    id: str
    imperative: str
    scope: Scope = "high-risk"
    status: CoreStatus = "seed"
    source: str = ""


@dataclass(frozen=True)
class Core:
    """记录从 ``core.md`` 读取的行为要求集合。

    Attributes:
        items: 保持文件顺序的 Core 条目。
    """

    items: tuple[CoreItem, ...]


@dataclass(frozen=True)
class Profile:
    """记录用户维护的画像。

    AI 只能通过建议队列提案，不能直接改写画像正文。

    Attributes:
        ability: 用户能力水平。
        methodology: 用户的方法论偏好。
        expression: 用户的表达风格偏好。
        goal: 用户的长期目标。
        other: 不属于前四个维度的画像内容。
        updated: 画像更新时间文本。
        source: 最后一次写入路径的性质，不表示各字段各自的来源；读取时先转为
            文本，假值回退为 ``user-edit``，其他值保留；写入时由调用参数
            覆盖并按 ``ProfileSource`` 校验。
    """

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    updated: str = ""
    source: str = "user-edit"


@dataclass(frozen=True)
class Suggestion:
    """记录一条 AI 画像建议。

    接受或丢弃后的记录仍留在队列中供审计。

    Attributes:
        id: 用于匹配状态更新的标识；队列允许重复，同 ID 记录会一并更新。
        dimension: 建议要更新的画像维度。
        body: 建议写入该维度的正文。
        sources: 支持建议的来源引用。
        date: 建议生成日期文本。
        status: 建议当前的待处理、接受或丢弃状态。
        policy_version: 生成建议所用的门禁策略版本；旧记录缺失该字段时按
            ``1`` 读取，但不原地回写。
    """

    id: str
    dimension: ProfileDimension
    body: str
    sources: tuple[str, ...] = ()
    date: str = ""
    status: SuggestionStatus = "pending"
    policy_version: int = 1


@dataclass(frozen=True)
class Note:
    """记录一条二层可复用知识。

    ``memory_id`` 是跨标题和文件重命名的稳定身份；引用计数是从日志重建的
    缓存，不是事实源。

    Attributes:
        type: frontmatter 类型标记，固定为 ``note``。
        title: 知识标题。
        tags: 用于检索和筛选的标签。
        kind: 知识类别。
        summary: 用于索引和检索的摘要。
        created: 首次创建日期文本。
        updated: 最近一次内容或判断更新的日期文本。
        verification: 当前结论的证据强度。
        verification_reason: 选择该证据强度的理由。
        pain: 相关问题造成的损失或成本评分。
        pain_reason: 给出损失或成本评分的理由。
        conflicts_with: 与本结论冲突的 Note 文件 stem；不同于持久化
            ``memory_id``。
        memory_id: 不随标题或文件 stem 变化的持久化身份。
        status: 唯一的生命周期状态；``retired`` 和置信度不另设存储轴。
        supersedes: 被本 Note 取代的持久化 Note 身份。
        superseded_by: 取代本 Note 的持久化 Note 身份。
        valid_from: 当前结论开始生效的日期文本。
        last_verified_at: 最近一次重新验证结论的日期文本。
        refs: 可归因读取事件数。
        read_sessions: 实际读取过本 Note 的独立用户会话数。
        helpful_refs: 判定本 Note 有帮助的独立用户会话数。
        harmful_refs: 判定本 Note 有害的独立用户会话数。
        last_ref: 最近一次可归因用户读取的本地日期文本。
        trigger: 适用本知识的触发条件。
        do_not_use_when: 不应使用本知识的条件。
        sources: 知识来源标识。
        source_sessions: 贡献过该知识的来源会话标识。
        source_segments: 贡献过该知识的已封口片段标识。
        derivations: 生成或更新该知识的模型运行来源。
        content_hash: 同一来源会话内识别重复知识所用的内容摘要。
        body: 知识的 Markdown 正文。
    """

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
    status: NoteStatus = "active"
    supersedes: tuple[str, ...] = ()
    superseded_by: str = ""
    valid_from: str = ""
    last_verified_at: str = ""
    refs: int = 0
    read_sessions: int = 0
    helpful_refs: int = 0
    harmful_refs: int = 0
    last_ref: str = ""
    trigger: str = ""
    do_not_use_when: str = ""
    sources: tuple[str, ...] = ()
    source_sessions: tuple[str, ...] = ()
    source_segments: tuple[str, ...] = ()
    derivations: tuple[DerivationProvenance, ...] = ()
    content_hash: str = ""
    body: str = ""


@dataclass(frozen=True)
class Diary:
    """记录一份按日、周或月保存的经历。

    Attributes:
        type: frontmatter 类型标记，固定为 ``diary``。
        date: 日记的存储键；日层为日期，周层和月层为对应周期值。
        layer: 日、周或月聚合层级。
        period: 聚合覆盖的周期文本。
        promoted_knowledge: 已从日记晋升出的知识标识。
        body: 日记的 Markdown 正文。
    """

    type: Literal["diary"]
    date: str
    layer: DiaryLayer = "day"
    period: str = ""
    promoted_knowledge: tuple[str, ...] = ()
    body: str = ""


@dataclass(frozen=True)
class DictionaryEntry:
    """记录 Dictionary 索引文件的分类信息。

    Attributes:
        type: frontmatter 类型标记，固定为 ``dictionary``。
        layer: ``L0`` 总览或 ``L1`` 领域页。
        domain: ``L1`` 条目所属领域；总览可留空。
    """

    type: Literal["dictionary"]
    layer: DictionaryLayer
    domain: str = ""


@dataclass(frozen=True)
class PersistContext:
    """记录一次提炼落盘可使用的完整来源上下文。

    persist 只能使用这些已确认的来源字段，不能自行推断。

    Attributes:
        segment_id: 来源片段的稳定标识；用作 Episode 原位更新键和 completion
            manifest 文件键，并随已封口来源写入 Note 归因。
        cc_session_id: 兼容字段，记录来源原生会话标识；Codex 来源使用 thread ID。
        workdir: 来源会话登记的工作目录。
        registered_at: 来源会话的登记时间文本。
        review_date: 调用方指定的 review 批次日期，用作 Note 日期并写入
            Episode 与 completion manifest。
        source_jsonl: 来源日志或 journal 的路径文本。
        source_start_offset: Claude Code 来源 JSONL 半开字节区间的起点；Codex
            不使用该字段定位来源。
        source_end_offset: Claude Code 来源 JSONL 半开字节区间的终点；
            ``None`` 表示直到文件末尾，Codex 不使用该字段定位来源。
        activity_dates: 当前片段允许归属的日历日；优先来自事件时间，必要时按
            ``date_basis`` 回退到完成或登记时间。为空表示无法确认归属日期，
            非空 Diary 日期会因此被判为越界。
        date_basis: ``activity_dates`` 所依据的时间来源；兼容上下文可留空。
        processed_date: 实际完成提炼的日期，仅写入 Episode 片段供审计。
        completed_segment: 已封口片段的 host-neutral 来源记录。
        derivation: 本次提炼使用的 runtime、模型和流水线来源。
    """

    segment_id: str
    cc_session_id: str
    workdir: str
    registered_at: str
    review_date: str
    source_jsonl: str
    source_start_offset: int = 0
    source_end_offset: int | None = None
    activity_dates: tuple[str, ...] = ()
    date_basis: str = ""
    processed_date: str = ""
    completed_segment: CompletedSegment | None = None
    derivation: DerivationProvenance | None = None
