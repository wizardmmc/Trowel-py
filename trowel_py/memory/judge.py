"""per-session judge (判效) orchestration (slice-053).

The sister step of review_job.run_one_session: right after a session is
distilled, ``judge_session`` spawns ANOTHER cc agent (session_kind="eval" — C-3)
that reads the SAME session jsonl and asks how trowel's memory was used — used?
helpful? should-have-used-but-didn't? Its structured verdict lands at
``meta/judgements/<cc_session_id>.json`` and feeds the soft metrics.

Why a separate agent (C-1): judging existing notes and producing new ones are
two different jobs; mixing them blurs responsibility and raises hallucination.
038 sketched this as reflection; 053 runs it for real, independent.

The cc host is injectable (``host_factory``) so tests never spawn a real cc
(#46416). C-2: any failure is caught and returns None — the judge is an
extension of review, never a precondition, so a judge crash can never block
review's ``advance_extracted``.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

from trowel_py.memory.access_log import AccessRecord, read_access_log
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.judge_prompt import build_judge_prompt
from trowel_py.memory.judgements import (
    VALID_ATTRIBUTIONS,
    VALID_OUTCOMES,
    HitJudgement,
    JudgementReport,
    MissJudgement,
    drop_unknown_memory_ids,
    save_judgement_report,
)
from trowel_py.memory.sessions_repo import SessionRecord
from trowel_py.memory.store import MemoryStore

logger = logging.getLogger(__name__)

#: a callable that builds a cc host for one session in its judge workdir.
#: Tests inject a fake; production leaves it None → a real CCHost is built.
HostFactory = Callable[[SessionRecord, Path], Any]

_JUDGE_WORKDIR_NAME = "judge-work"
_DRAFT_FILE = "judgement-draft.json"


class JudgeError(Exception):
    """The judge agent did not finish cleanly / produced no / a bad draft.

    judge_session catches this (and any other Exception) and returns None so a
    judge failure never breaks review (C-2). Kept as a named exception only so
    the inner function can signal the typed failure mode to itself.
    """


def _ensure_judge_workdir(
    date_str: str, memory_root: Path, cc_session_id: str
) -> Path:
    """Create the dated judge workdir (sibling of review-daily-work)."""
    workdir = memory_root.parent / _JUDGE_WORKDIR_NAME / date_str / cc_session_id
    workdir.mkdir(parents=True, exist_ok=True)
    return workdir


def _coerce_bool(value: object) -> bool:
    """Tolerantly coerce an agent-supplied ``used`` to bool.

    The draft may emit ``true``/``false`` as JSON booleans OR as the strings
    ``"true"``/``"false"``; a naive ``bool("false")`` would be True. Accept the
    obvious string spellings, else fall back to truthiness.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


def _summarize_access_log(
    root: Path, cc_session_id: str, index: AttributionIndex
) -> str:
    """Pre-extract the judged session's retrieval history as hard evidence (C-3).

    Gathers every access-log record that resolves to ``cc_session_id`` via the
    attribution index — by trowel binding OR by the record's own cc_session_id
    — so the judge's own eval-kind reads never appear here. slice-061: this no
    longer relies on a non-empty cc_session_id; records written before cc init
    (empty cc_session_id) are pulled in through their trowel binding (C-3).
    Empty when the session never touched memory — that is itself signal, not an
    error.
    """
    records = [
        r
        for r in read_access_log(root)
        if index.resolve(r.trowel_session_id, r.cc_session_id).cc_session_id
        == cc_session_id
    ]
    if not records:
        return "（该会话没有检索记录：没 search 也没 read）"

    # group search candidates by their search_id → query.
    by_search: dict[str, list[AccessRecord]] = defaultdict(list)
    queries: dict[str, str] = {}
    reads: list[AccessRecord] = []
    for r in records:
        if r.action == "search":
            by_search[r.search_id].append(r)
            queries.setdefault(r.search_id, r.query)
        elif r.action == "read":
            reads.append(r)

    lines: list[str] = []
    if by_search:
        lines.append("search:")
        for sid, recs in by_search.items():
            q = queries.get(sid, "")
            cands = sorted({r.memory_id for r in recs if r.memory_id})
            cand_text = ", ".join(cands) if cands else "(无候选)"
            lines.append(f"  - query={q!r} 候选=[{cand_text}]")
    if reads:
        read_ids = [r.memory_id for r in reads if r.memory_id]
        lines.append(f"read: {len(reads)} 条 -> {read_ids}")
    return "\n".join(lines)


def _dictionary_index(store: MemoryStore) -> str:
    """The existing-notes index fed to the judge (C-6 ground truth + context).

    Prefers the regenerated dictionary L0 (the same index the model drills);
    falls back to a flat ``memory_id: summary`` list when no dictionary exists
    yet (cold start) so the judge still has the real id set to ground memory_ids.
    """
    l0 = store.load_dictionary_L0().strip()
    if l0:
        return l0
    rows = [
        (n.memory_id, n.summary)
        for _stem, n in store.load_notes_with_id()
        if n.memory_id
    ]
    if not rows:
        return "（暂无笔记）"
    return "\n".join(f"- {mid}: {summary}" for mid, summary in rows)


def _parse_draft(text: str, *, cc_session_id: str, segment_id: str = "") -> JudgementReport:
    """Parse the agent's judgement-draft.json into a JudgementReport (loose).

    Tolerant of shape wobble: a bad outcome collapses to ``unknown`` (kept with
    its reason/evidence), a bad/missing attribution drops the miss entirely
    (a miss without a valid attribution is not actionable — C-7). memory_id
    validity is NOT checked here — that is the C-6 backstop applied next.

    Raises:
        JudgeError: the draft is not valid JSON.
    """
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JudgeError(f"judgement-draft.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise JudgeError("judgement-draft.json is not a JSON object")

    hits: list[HitJudgement] = []
    for item in data.get("hits", []) or []:
        if not isinstance(item, dict):
            continue
        outcome = item.get("outcome")
        if outcome not in VALID_OUTCOMES:
            outcome = "unknown"
        hits.append(
            HitJudgement(
                memory_id=str(item.get("memory_id") or ""),
                used=_coerce_bool(item.get("used")),
                outcome=outcome,  # type: ignore[arg-type]
                reason=str(item.get("reason") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    miss: list[MissJudgement] = []
    for item in data.get("recall_miss", []) or []:
        if not isinstance(item, dict):
            continue
        attribution = item.get("attribution")
        if attribution not in VALID_ATTRIBUTIONS:
            # a miss without a valid attribution is dropped (C-7: novelty etc.
            # must not leak in as an unattributed miss).
            continue
        miss.append(
            MissJudgement(
                memory_id=str(item.get("memory_id") or ""),
                attribution=attribution,  # type: ignore[arg-type]
                reason=str(item.get("reason") or ""),
                evidence=str(item.get("evidence") or ""),
            )
        )

    return JudgementReport(
        cc_session_id=cc_session_id,
        hits=tuple(hits),
        recall_miss=tuple(miss),
        summary=str(data.get("summary") or ""),
        segment_id=segment_id,
    )


async def _judge_session_inner(
    session: SessionRecord,
    review_date: str,
    memory_root: Path,
    host_factory: HostFactory | None,
    segment_id: str = "",
) -> JudgementReport:
    """Spawn the eval agent, parse + C-6 filter + save. Raises on failure."""
    store = MemoryStore(memory_root)
    index = AttributionIndex.from_root(memory_root)
    access_summary = _summarize_access_log(
        memory_root, session.cc_session_id, index
    )
    dict_index = _dictionary_index(store)
    prompt = build_judge_prompt(
        session.jsonl_path or "", access_summary, dict_index
    )

    workdir = _ensure_judge_workdir(review_date, memory_root, session.cc_session_id)

    if host_factory is not None:
        host = host_factory(session, workdir)
    else:
        from trowel_py.cc_host.service import CCHost
        from trowel_py.memory.mcp_config import write_mcp_config

        # proxy_base_url None: the judge runs as a CLI/timer extension of review
        # (no trowel proxy lifespan); cc reads ~/.claude/settings.json directly
        # (mirrors review_job). session_kind="eval" (C-3): this agent's OWN
        # access-log lands under a different cc_session_id and never feeds the
        # judged session's metrics; it is also excluded from find_incremental.
        host = CCHost(
            session_id=uuid.uuid4().hex,
            workdir=str(workdir),
            session_kind="eval",
            mcp_config=str(write_mcp_config()),
        )

    finished = False
    try:
        # No explicit asyncio timeout here: a wedged cc is killed by CCHost's
        # stalled detector (stalled_threshold_kill, 30min) which emits an
        # ErrorEvent → finished stays False → JudgeError → None (C-2). This
        # mirrors review_job.run_one_session (same host, same backstop); adding
        # a judge-only timeout would diverge from review for no correctness gain.
        async for event in host.send(prompt):
            if getattr(event, "type", None) == "finished":
                finished = True
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()

    if not finished:
        raise JudgeError(
            f"judge agent did not finish cleanly for {session.cc_session_id}"
        )

    draft_path = workdir / _DRAFT_FILE
    if not draft_path.exists():
        raise JudgeError(
            f"judge agent produced no {_DRAFT_FILE} for {session.cc_session_id}"
        )
    report = _parse_draft(
        draft_path.read_text(encoding="utf-8"),
        cc_session_id=session.cc_session_id,
        segment_id=segment_id,
    )

    # C-6: drop judgements whose memory_id is not a real note.
    known_ids = frozenset(
        n.memory_id
        for _stem, n in store.load_notes_with_id()
        if n.memory_id
    )
    report = drop_unknown_memory_ids(report, known_ids)

    save_judgement_report(memory_root, report)
    logger.info(
        "judge: %s -> %d hit(s), %d recall-miss",
        session.cc_session_id,
        len(report.hits),
        len(report.recall_miss),
    )
    return report


async def judge_session(
    session: SessionRecord,
    review_date: str,
    memory_root: Path,
    *,
    host_factory: HostFactory | None = None,
    segment_id: str = "",
) -> JudgementReport | None:
    """Judge one session's memory usage; None on any failure (C-2).

    Args:
        session: the SessionRecord just distilled (judged in place).
        review_date: labels the judge workdir.
        memory_root: memory root (notes + access-log + judgements land here).
        host_factory: optional ``(session, workdir) -> cc host``. None → a real
            CCHost (session_kind="eval") built in the session's judge workdir.

    Returns:
        The saved JudgementReport, or None if the agent errored / produced no
        draft / the draft was unparseable. A None NEVER breaks the caller's
        review flow — the judge is an extension of review, not a precondition.
    """
    try:
        return await _judge_session_inner(
            session, review_date, memory_root, host_factory, segment_id
        )
    except Exception as exc:  # noqa: BLE001 — C-2: isolate every failure mode
        logger.warning(
            "judge failed for %s (isolated; review unaffected): %s",
            session.cc_session_id,
            exc,
        )
        return None
