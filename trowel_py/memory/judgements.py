"""judgement report dataclass + IO + the C-6 memory_id hard-check (slice-053).

A JudgementReport is one judged session's verdict on how trowel's memory was
used: the hit notes (used? helpful/harmful?) and the recall-misses (a relevant
note that went unused, attributed to retrieval vs awareness). Every judgement
carries a reason + the session step that backs it (C-4 — traceable, not
sampled).

Reports land one-per-judged-session at ``meta/judgements/<cc_session_id>.json``
(C-3 — keyed by the judged session's cc_session_id so the judge's OWN
access-log, recorded under a different eval-kind cc_session_id, never lands in
the judged session's metrics).

The C-6 backstop ``drop_unknown_memory_ids`` keeps judgements whose memory_id
is a real note and silently drops fabricated ones — the agent can hallucinate
an id, but it cannot pollute the metrics with it. Mirrors tidy's
``_memory_id_to_stem`` id check (041).

This module is the strict layer: ``from_dict`` rejects unknown outcome /
attribution values loudly. The judge job parses the agent's loose draft and
calls into here with validated values.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_META_DIR = "meta"
_JUDGEMENTS_DIR = "judgements"

#: closed sets (mirrors judge_prompt so a corrupt on-disk value is rejected
#: with a clear message instead of a typing-layer surprise).
VALID_OUTCOMES: frozenset[str] = frozenset(
    {"helpful", "harmful", "unused", "unknown"}
)
VALID_ATTRIBUTIONS: frozenset[str] = frozenset(
    {"retrieval_miss", "awareness_miss"}
)

Outcome = Literal["helpful", "harmful", "unused", "unknown"]
Attribution = Literal["retrieval_miss", "awareness_miss"]


@dataclass(frozen=True)
class HitJudgement:
    """One hit note: did the model use it, and was that use helpful.

    Attributes:
        memory_id: the real note id (C-6 — fabricated ids are dropped upstream).
        used: did the model fold this note into a decision (not merely read it).
        outcome: helpful / harmful / unused / unknown.
        reason: why this verdict (C-4).
        evidence: the session step that backs it (C-4).
    """

    memory_id: str
    used: bool
    outcome: Outcome
    reason: str
    evidence: str


@dataclass(frozen=True)
class MissJudgement:
    """One recall-miss: a relevant note that went unused, with attribution.

    Attributes:
        memory_id: the real note id that SHOULD have been used.
        attribution: retrieval_miss (never surfaced) / awareness_miss (surfaced
            but not acted on). Novelty (no relevant note existed) is NOT a miss
            and never reaches here (C-7).
        reason: why this was a miss (C-4).
        evidence: the session step that would have been avoided (C-4).
    """

    memory_id: str
    attribution: Attribution
    reason: str
    evidence: str


@dataclass(frozen=True)
class JudgementReport:
    """One judged session's verdict across the three dimensions.

    Attributes:
        cc_session_id: the JUDGED session's cc id (keys the on-disk file + the
            metrics; the judge's own eval-kind id never appears here — C-3).
        hits: hit notes the model search/read (used? helpful?).
        recall_miss: relevant notes that went unused, attributed (C-7).
        summary: one-line human-readable take.
    """

    cc_session_id: str
    hits: tuple[HitJudgement, ...]
    recall_miss: tuple[MissJudgement, ...]
    summary: str


# ---------- serialization ----------


def _hit_to_dict(h: HitJudgement) -> dict[str, object]:
    return {
        "memory_id": h.memory_id,
        "used": h.used,
        "outcome": h.outcome,
        "reason": h.reason,
        "evidence": h.evidence,
    }


def _miss_to_dict(m: MissJudgement) -> dict[str, object]:
    return {
        "memory_id": m.memory_id,
        "attribution": m.attribution,
        "reason": m.reason,
        "evidence": m.evidence,
    }


def _hit_from_dict(d: dict[str, object]) -> HitJudgement:
    outcome = d.get("outcome")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r} in judgement hit")
    return HitJudgement(
        memory_id=str(d.get("memory_id") or ""),
        used=bool(d.get("used")),
        outcome=outcome,  # type: ignore[arg-type]
        reason=str(d.get("reason") or ""),
        evidence=str(d.get("evidence") or ""),
    )


def _miss_from_dict(d: dict[str, object]) -> MissJudgement:
    attribution = d.get("attribution")
    if attribution not in VALID_ATTRIBUTIONS:
        raise ValueError(f"unknown attribution {attribution!r} in judgement miss")
    return MissJudgement(
        memory_id=str(d.get("memory_id") or ""),
        attribution=attribution,  # type: ignore[arg-type]
        reason=str(d.get("reason") or ""),
        evidence=str(d.get("evidence") or ""),
    )


def _report_to_dict(r: JudgementReport) -> dict[str, object]:
    return {
        "cc_session_id": r.cc_session_id,
        "hits": [_hit_to_dict(h) for h in r.hits],
        "recall_miss": [_miss_to_dict(m) for m in r.recall_miss],
        "summary": r.summary,
    }


def _report_from_dict(d: dict[str, object]) -> JudgementReport:
    raw_hits_val = d.get("hits", [])
    raw_hits = raw_hits_val if isinstance(raw_hits_val, list) else []
    raw_miss_val = d.get("recall_miss", [])
    raw_miss = raw_miss_val if isinstance(raw_miss_val, list) else []
    return JudgementReport(
        cc_session_id=str(d.get("cc_session_id") or ""),
        hits=tuple(
            _hit_from_dict(h) for h in raw_hits if isinstance(h, dict)
        ),
        recall_miss=tuple(
            _miss_from_dict(m) for m in raw_miss if isinstance(m, dict)
        ),
        summary=str(d.get("summary") or ""),
    )


# ---------- IO ----------


def _judgement_path(root: Path, cc_session_id: str) -> Path:
    """Where one judged session's report lives (C-3 — keyed by judged id)."""
    return root / _META_DIR / _JUDGEMENTS_DIR / f"{cc_session_id}.json"


def save_judgement_report(root: Path | str, report: JudgementReport) -> None:
    """Persist one report to ``meta/judgements/<cc_session_id>.json``.

    Parent dirs are created. The report is the Python-validated truth (already
    C-6 filtered by the caller), so this is a plain overwrite.
    """
    path = _judgement_path(Path(root), report.cc_session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_report_to_dict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_judgement_report(
    root: Path | str, cc_session_id: str
) -> JudgementReport | None:
    """Return one session's report, or None if it was never judged.

    Raises:
        ValueError: the file exists but is corrupt (bad JSON, or a judgement
            carries an unknown outcome/attribution) — corruption is loud (C-6
            distinguishes "no data" from "damaged data").
    """
    path = _judgement_path(Path(root), cc_session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt judgement at {path}: {exc}") from exc
    return _report_from_dict(data)


def load_all_judgement_reports(root: Path | str) -> list[JudgementReport]:
    """Return every persisted report (the metrics' soft source).

    A corrupt single file is skipped (not fatal) so one bad report does not
    blank the whole metric. Missing dir → empty list.
    """
    d = Path(root) / _META_DIR / _JUDGEMENTS_DIR
    if not d.exists():
        return []
    out: list[JudgementReport] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(_report_from_dict(data))
        except (ValueError, json.JSONDecodeError, OSError):
            # corrupt JSON, unknown outcome/attribution, or unreadable file
            # (permissions / deleted-between-glob-and-read) — skip one bad
            # report, do NOT blank the whole metric.
            logger.warning("skipping corrupt judgement file: %s", p.name)
    return out


# ---------- C-6 backstop ----------


def drop_unknown_memory_ids(
    report: JudgementReport, known_ids: frozenset[str]
) -> JudgementReport:
    """Return a copy with judgements whose memory_id is NOT real removed (C-6).

    The judge agent can hallucinate a memory_id; this keeps it out of the
    metrics. Hits and misses are filtered independently; ``cc_session_id`` and
    ``summary`` are preserved. An id not in ``known_ids`` (the real notes'
    memory_ids) is treated as fabricated and dropped.

    Args:
        report: the parsed-but-unfiltered report from the agent draft.
        known_ids: the real notes' memory_ids (from ``store.load_notes_with_id``).

    Returns:
        A new JudgementReport with only known-id judgements.
    """
    kept_hits = tuple(h for h in report.hits if h.memory_id in known_ids)
    kept_miss = tuple(m for m in report.recall_miss if m.memory_id in known_ids)
    dropped = (len(report.hits) - len(kept_hits)) + (
        len(report.recall_miss) - len(kept_miss)
    )
    if dropped:
        logger.info(
            "dropped %d fabricated memory_id judgement(s) for %s",
            dropped,
            report.cc_session_id,
        )
    return replace(report, hits=kept_hits, recall_miss=kept_miss)
