"""rebuild note count caches from the access/outcome logs + judgement reports.

slice-065 turns this into a two-source, session-level evidence pipeline:
access/outcome (explicit evidence, keyed by note STEM) + segment judgement
(soft evidence, keyed by UUIDv7 memory_id). Both are attributed via
AttributionIndex; only USER sessions count, and effects dedupe at the
(cc_session_id, note) level — one session re-reading or re-judging the same
note is ONE session, and harmful beats helpful within a session. The logs +
judgement reports are the source of truth; ``refs`` / ``read_sessions`` /
``helpful_refs`` / ``harmful_refs`` / ``last_ref`` on each Note are rebuildable
caches (C-1). ``compute_note_effects`` is the single aggregation recompute,
promotion and metrics share so the three never disagree.

C-1 (040-c): only ``action=read`` counts as retrieved (and increments refs);
``action=search`` (candidate returned) does NOT. C-6: an unvoted read is
``unknown`` — never silently counted as helpful.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import tzinfo
from pathlib import Path
from typing import Any

from trowel_py.memory.access_log import read_access_log, read_outcome_log
from trowel_py.memory.activity_dates import _parse_iso_to_date, _system_local_tz
from trowel_py.memory.attribution import AttributionIndex
from trowel_py.memory.judgements import load_all_judgement_reports
from trowel_py.memory.store import MemoryStore


@dataclass(frozen=True)
class NoteEffect:
    """One note's session-level usage effect, rebuilt from the logs (slice-065).

    The logs + judgement reports are the source of truth; this per-note
    aggregation is what recompute, promotion and metrics all consume, so they
    never disagree about what the evidence says.

    Attributes:
        stem: the note's filename stem (the aggregation key — access/outcome
            log memory_id IS the stem; judgement memory_id is UUIDv7, mapped
            back to stem via the note's memory_id).
        memory_id: the note's UUIDv7 ("" if the note predates migrate).
        refs: user read EVENTS (not sessions).
        read_sessions: distinct user cc_session_ids that read this note.
        helpful_sessions: distinct user cc_session_ids with a helpful
            session-level effect (outcome OR judgement, used=true).
        harmful_sessions: same for harmful. C-3: a session that is BOTH
            helpful and harmful counts ONLY as harmful (never both).
        read_dates: distinct local dates of ALL user reads (drives last_ref).
        helpful_read_dates: dates of reads in sessions with a helpful effect —
            this is what ``distinct_days`` counts (a same-day unknown read must
            not make single-day helpful evidence look multi-day, C-7).
        unused_sessions: distinct user cc sessions that read but had no
            helpful/harmful effect (outcome OR judgement unused). Coverage, not
            a vote — never counted as helpful or harmful.
    """

    stem: str
    memory_id: str
    refs: int
    read_sessions: frozenset[str]
    helpful_sessions: frozenset[str]
    harmful_sessions: frozenset[str]
    unused_sessions: frozenset[str]
    read_dates: frozenset[str]
    helpful_read_dates: frozenset[str]

    @property
    def read_session_count(self) -> int:
        return len(self.read_sessions)

    @property
    def helpful_refs(self) -> int:
        return len(self.helpful_sessions)

    @property
    def harmful_refs(self) -> int:
        return len(self.harmful_sessions)

    @property
    def unused_refs(self) -> int:
        return len(self.unused_sessions)

    @property
    def distinct_days(self) -> int:
        return len(self.helpful_read_dates)

    @property
    def last_ref(self) -> str:
        return max(self.read_dates) if self.read_dates else ""


def compute_note_effects(
    root: Path | str, *, local_tz: tzinfo | None = None
) -> dict[str, NoteEffect]:
    """Rebuild every note's session-level effect from logs + judgements.

    Two evidence sources (slice-065 §1):

    - explicit: outcome-log, which MUST link to a real read_id. The outcome
      inherits the SESSION of the read it links to (not the outcome record's
      own identity, which can differ across a resume). An outcome whose
      read_id matches no access-log read is dropped (§1).
    - judged: slice-061 segment judgement, only hits with ``used=true`` on a
      real memory_id (UUIDv7 → stem).

    Both sources are attributed via AttributionIndex; only USER sessions count
    (review/distill/eval never promote themselves — C-4). Effects dedupe at the
    (cc_session_id, note) session level: one session re-reading 10x or
    re-judging the same segment is ONE session (C-2). Harmful wins over
    helpful within a session so counter-evidence cannot be outvoted (C-3).

    Args:
        root: the memory root directory.
        local_tz: timezone for the day boundary (None → system local; inject
            in tests so ``last_ref`` / ``distinct_days`` are deterministic).

    Returns:
        ``{stem: NoteEffect}`` for every note with any user activity. Notes
        with no activity are absent — the caller resets their caches.
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    notes = dict(store.load_notes_with_id())  # stem -> Note
    id_to_stem = {n.memory_id: stem for stem, n in notes.items() if n.memory_id}
    index = AttributionIndex.from_root(root_path)
    tz = local_tz or _system_local_tz()

    read_events: dict[str, int] = defaultdict(int)
    read_sessions_map: dict[str, set[str]] = defaultdict(set)
    read_dates_map: dict[str, set[str]] = defaultdict(set)
    # (stem, cc) -> read dates: so distinct_days can count ONLY the days that
    # helpful-evidence sessions read, not every read (C-7).
    read_dates_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    # read_id -> (stem, cc): the session a read belongs to, so an outcome can
    # be attributed back to the session that did the reading (not the outcome
    # record's own possibly-different identity across a resume).
    reads_by_id: dict[str, tuple[str, str]] = {}

    for rec in read_access_log(root_path):
        attr = index.resolve(rec.trowel_session_id, rec.cc_session_id)
        if not attr.is_user:
            continue
        cc = attr.cc_session_id or ""
        if rec.action == "read" and rec.memory_id and rec.memory_id in notes:
            stem = rec.memory_id
            read_events[stem] += 1
            read_sessions_map[stem].add(cc)
            day = _parse_iso_to_date(rec.ts, tz) or ""
            if day:
                read_dates_map[stem].add(day)
                read_dates_by_pair[(stem, cc)].add(day)
            if rec.read_id:
                reads_by_id[rec.read_id] = (stem, cc)

    pair_helpful: set[tuple[str, str]] = set()
    pair_harmful: set[tuple[str, str]] = set()
    pair_unused: set[tuple[str, str]] = set()

    # explicit evidence: outcome must link to a real read (§1) AND come from
    # the same user session that did the reading (C-4 — a non-user session
    # must not inject evidence by quoting a user's read_id; defense-in-depth,
    # since read_id is a server-generated uuid in practice).
    for orec in read_outcome_log(root_path):
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

    # judged evidence: memory_id is UUIDv7 → stem. helpful/harmful require
    # used=true (§1); unused is coverage (model saw it but did not use it) and
    # is counted regardless of `used`.
    for report in load_all_judgement_reports(root_path):
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

    # C-3: within one (note, session) harmful wins, then helpful; unused is
    # recorded only when the session had neither.
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

    # distinct_days counts only the days of HELPFUL-evidence sessions (C-7).
    helpful_read_dates_map: dict[str, set[str]] = defaultdict(set)
    for stem, ccs in helpful_sessions.items():
        for cc in ccs:
            helpful_read_dates_map[stem] |= read_dates_by_pair.get(
                (stem, cc), set()
            )

    effects: dict[str, NoteEffect] = {}
    touched = (
        set(read_events) | set(helpful_sessions)
        | set(harmful_sessions) | set(unused_sessions_map)
    )
    for stem in touched:
        if stem not in notes:
            continue
        note = notes[stem]
        effects[stem] = NoteEffect(
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


def recompute_counters(
    root: Path | str, *, local_tz: tzinfo | None = None
) -> dict[str, Any]:
    """Rebuild every note's count caches from the logs + judgements (slice-065).

    Delegates to ``compute_note_effects`` (the single source of evidence) and
    writes the session-level results back as the note's
    ``refs`` / ``read_sessions`` / ``helpful_refs`` / ``harmful_refs`` /
    ``last_ref`` caches. A note with a stale non-zero cache but no surviving
    evidence is reset to 0 (C-1 — the cache is never the truth).

    Args:
        root: the memory root directory.
        local_tz: timezone for the day boundary (None → system local).

    Returns:
        A report dict: ``updated`` (notes rewritten), ``refs_total`` (user read
        events), ``read_sessions_total``, ``helpful_total``, ``harmful_total``
        (the latter two are independent-session counts, not raw events).
    """
    root_path = Path(root)
    store = MemoryStore(root_path)
    effects = compute_note_effects(root_path, local_tz=local_tz)

    # write back every note with activity; also reset stale caches on notes
    # that have a non-zero cache but no surviving evidence (C-1 truth).
    touched = set(effects)
    for stem, note in store.load_notes_with_id():
        if stem in touched:
            continue
        if (
            note.refs or note.read_sessions or note.helpful_refs
            or note.harmful_refs or note.last_ref
        ):
            touched.add(stem)

    updated = 0
    refs_total = 0
    read_sessions_total = 0
    helpful_total = 0
    harmful_total = 0
    for stem in touched:
        if store.load_note(stem) is None:
            continue  # log references a deleted note — skip, don't crash
        eff = effects.get(stem)
        if eff is None:
            fields: dict[str, Any] = {
                "refs": 0,
                "read_sessions": 0,
                "helpful_refs": 0,
                "harmful_refs": 0,
                "last_ref": "",
            }
        else:
            fields = {
                "refs": eff.refs,
                "read_sessions": eff.read_session_count,
                "helpful_refs": eff.helpful_refs,
                "harmful_refs": eff.harmful_refs,
                "last_ref": eff.last_ref,
            }
            refs_total += eff.refs
            read_sessions_total += eff.read_session_count
            helpful_total += eff.helpful_refs
            harmful_total += eff.harmful_refs
        store.update_note_fields(stem, fields)
        updated += 1

    return {
        "updated": updated,
        "refs_total": refs_total,
        "read_sessions_total": read_sessions_total,
        "helpful_total": helpful_total,
        "harmful_total": harmful_total,
    }
