"""从读取、反馈和 judgement 记录重建 note 的效果证据。"""

from __future__ import annotations

from collections import defaultdict
from datetime import tzinfo
from pathlib import Path
from typing import Any

from trowel_py.memory.access_log import read_access_log, read_outcome_log
from trowel_py.memory.activity_dates import _parse_iso_to_date, _system_local_tz
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.judgements import load_all_judgement_reports
from trowel_py.memory.recompute import NoteEffect
from trowel_py.memory.store import MemoryStore


def compute_note_effects(
    root: Path | str,
    *,
    local_tz: tzinfo | None = None,
    store_cls: Any = MemoryStore,
    attribution_index_cls: Any = AttributionIndex,
    system_local_tz_fn: Any = _system_local_tz,
    parse_iso_to_date_fn: Any = _parse_iso_to_date,
    read_access_log_fn: Any = read_access_log,
    read_outcome_log_fn: Any = read_outcome_log,
    load_reports_fn: Any = load_all_judgement_reports,
    effect_cls: Any = NoteEffect,
) -> dict[str, NoteEffect]:
    """按 note 聚合可归因于用户会话的效果证据。

    access/read 的 ``memory_id`` 直接作为当前 note stem；outcome 只通过
    ``read_id`` 继承该 read 的 note 与会话，自带的 ``memory_id`` 不参与定位。
    judgement 的 ``memory_id`` 才是稳定的 ``Note.memory_id``，需要映射回当前
    stem。同一 note 与会话出现多种结论时，按
    ``harmful > helpful > unused`` 保留一种。

    Args:
        root: memory 根目录。
        local_tz: 读取时间采用的时区；省略时使用系统本地时区。
        store_cls: 用于加载 note 的存储实现。
        attribution_index_cls: 用于判定记录所属会话类型的索引实现。
        system_local_tz_fn: 返回系统本地时区的函数。
        parse_iso_to_date_fn: 将日志时间换算为本地日期的函数。
        read_access_log_fn: 读取 access 记录的函数。
        read_outcome_log_fn: 读取 outcome 记录的函数。
        load_reports_fn: 加载 judgement 报告的函数。
        effect_cls: 构造单条聚合结果的类型。

    Returns:
        以当前 note stem 为键的效果。没有有效证据或已不存在的 note 不会出现。
    """
    root_path = Path(root)
    store = store_cls(root_path)
    notes = dict(store.load_notes_with_id())
    id_to_stem = {n.memory_id: stem for stem, n in notes.items() if n.memory_id}
    index = attribution_index_cls.from_root(root_path)
    tz = local_tz or system_local_tz_fn()

    read_events: dict[str, int] = defaultdict(int)
    read_sessions_map: dict[str, set[str]] = defaultdict(set)
    read_dates_map: dict[str, set[str]] = defaultdict(set)
    # 有帮助日期必须来自同一 note 和会话的真实读取，不能借用该会话的其他读取。
    read_dates_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    # note 和计数会话取自关联 read；outcome 解析出的会话仍须与读取会话一致，
    # 避免会话恢复后的身份漂移。
    reads_by_id: dict[str, tuple[str, str]] = {}

    for rec in read_access_log_fn(root_path):
        attr = index.resolve(rec.trowel_session_id, rec.cc_session_id)
        if not attr.is_user:
            continue
        cc = attr.cc_session_id or ""
        if rec.action == "read" and rec.memory_id and rec.memory_id in notes:
            stem = rec.memory_id
            read_events[stem] += 1
            read_sessions_map[stem].add(cc)
            day = parse_iso_to_date_fn(rec.ts, tz) or ""
            if day:
                read_dates_map[stem].add(day)
                read_dates_by_pair[(stem, cc)].add(day)
            if rec.read_id:
                reads_by_id[rec.read_id] = (stem, cc)

    pair_helpful: set[tuple[str, str]] = set()
    pair_harmful: set[tuple[str, str]] = set()
    pair_unused: set[tuple[str, str]] = set()

    # 只有来自原读取会话、且指向真实 read_id 的反馈才是有效显式证据。
    for orec in read_outcome_log_fn(root_path):
        linked = reads_by_id.get(orec.read_id)
        if linked is None:
            continue
        _rstem, reader_cc = linked
        oattr = index.resolve(orec.trowel_session_id, orec.cc_session_id)
        if not oattr.is_user or (oattr.cc_session_id or "") != reader_cc:
            continue
        if orec.outcome == "helpful":
            pair_helpful.add(linked)
        elif orec.outcome == "harmful":
            pair_harmful.add(linked)
        elif orec.outcome == "unused":
            pair_unused.add(linked)

    # judgement 用稳定 memory_id 定位当前 stem；正负结论要求 used，unused 不受其限制。
    for report in load_reports_fn(root_path):
        cc = report.cc_session_id
        if not cc:
            continue
        if not index.resolve("", cc).is_user:
            continue
        for hit in report.hits:
            jstem = id_to_stem.get(hit.memory_id)
            if jstem is None:
                continue
            pair = (jstem, cc)
            if hit.used and hit.outcome == "helpful":
                pair_helpful.add(pair)
            elif hit.used and hit.outcome == "harmful":
                pair_harmful.add(pair)
            elif hit.outcome == "unused":
                pair_unused.add(pair)

    # 每个 note/会话只保留一种结论：harmful 优先，其次 helpful，最后 unused。
    helpful_sessions: dict[str, set[str]] = defaultdict(set)
    harmful_sessions: dict[str, set[str]] = defaultdict(set)
    unused_sessions_map: dict[str, set[str]] = defaultdict(set)
    for pair in pair_helpful | pair_harmful | pair_unused:
        stem, cc = pair
        if pair in pair_harmful:
            harmful_sessions[stem].add(cc)
        elif pair in pair_helpful:
            helpful_sessions[stem].add(cc)
        else:
            unused_sessions_map[stem].add(cc)

    # distinct_days 只采用有帮助会话实际读取该 note 的日期。
    helpful_read_dates_map: dict[str, set[str]] = defaultdict(set)
    for stem, ccs in helpful_sessions.items():
        for cc in ccs:
            helpful_read_dates_map[stem] |= read_dates_by_pair.get((stem, cc), set())

    effects: dict[str, NoteEffect] = {}
    touched = (
        set(read_events)
        | set(helpful_sessions)
        | set(harmful_sessions)
        | set(unused_sessions_map)
    )
    for stem in touched:
        if stem not in notes:
            continue
        note = notes[stem]
        effects[stem] = effect_cls(
            stem=stem,
            memory_id=note.memory_id,
            refs=read_events.get(stem, 0),
            read_sessions=frozenset(read_sessions_map.get(stem, ())),
            helpful_sessions=frozenset(helpful_sessions.get(stem, ())),
            harmful_sessions=frozenset(harmful_sessions.get(stem, ())),
            unused_sessions=frozenset(unused_sessions_map.get(stem, ())),
            read_dates=frozenset(read_dates_map.get(stem, ())),
            helpful_read_dates=frozenset(helpful_read_dates_map.get(stem, ())),
        )
    return effects
