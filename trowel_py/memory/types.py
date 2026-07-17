"""value objects for the memory store (slice-038).

Frozen dataclasses throughout (immutability — see global coding-style). The
frontmatter field names reuse the wiki-compatible subset (title/tags/summary/
confidence/created/updated) and extend it with memory-only fields
(verification/refs/last_ref/retired/pain).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: a note id is its filename stem (stable, human-readable).
NoteId = str

EntryType = Literal["core", "note", "diary", "dictionary"]

Verification = Literal["verified", "inferred-untested", "event-data-supported"]
DiaryLayer = Literal["day", "week", "month"]
DictionaryLayer = Literal["L0", "L1"]
Scope = Literal["high-risk", "low-risk"]
#: slice-041: layer-one gains a `trial` state (monthly promote → approve →
#: trial → activate → active). seed is the one-time bootstrap seed (038).
CoreStatus = Literal["seed", "trial", "active", "retired"]
NoteKind = Literal["fact", "gotcha", "procedure", "preference", "hypothesis"]
#: slice-041: layer-two note lifecycle (C-9). `candidate` was removed (grill
#: 2026-07-11 — notes are for the model, not human-reviewed, so no "awaiting
#: confirmation" state; new notes default to `active`).
NoteStatus = Literal["active", "contradicted", "superseded", "retired"]
#: slice-047: profile.md write-path tag (immutability routing). The body is
#: always user-blessed; this tags the NATURE of the last commit, not per-field
#: origin. user-edit = the user typed/edited directly; ai-calibration = the
#: last commit was an accepted AI proposal merge (→ 050).
ProfileSource = Literal["user-edit", "ai-calibration"]
#: slice-050: the five profile dimensions a suggestion targets (mirrors the
#: Profile fields). Used by the suggestion queue + the distill agent's output.
ProfileDimension = Literal["ability", "methodology", "expression", "goal", "other"]
#: slice-050: lifecycle of a profile suggestion in the candidate queue.
#: pending = not yet seen by the user; accepted = merged into profile.md;
#: discarded = user rejected it (kept for audit, not deleted).
SuggestionStatus = Literal["pending", "accepted", "discarded"]


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating one frontmatter payload.

    Attributes:
        ok: True iff the payload satisfies the schema for its type.
        errors: human-readable validation failures (empty when ok).
    """

    ok: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoreItem:
    """One layer-one imperative (a value / hard rule).

    Attributes:
        id: stable identifier used in promoted_knowledge / refs.
        imperative: the rule text, imperative voice.
        scope: high-risk rules walk the full lookup chain; low-risk allow fast
            assumptions (防 ownership 表演).
        status: seed (probation) | active | retired.
        source: where this seed was derived from.
    """

    id: str
    imperative: str
    scope: Scope = "high-risk"
    status: CoreStatus = "seed"
    source: str = ""


@dataclass(frozen=True)
class Core:
    """Layer-one container: the always-injected imperatives."""

    items: tuple[CoreItem, ...]


@dataclass(frozen=True)
class Profile:
    """User self-description (slice-047): the集中 editable "who you are" file.

    Path-平级 to ``core.md`` (``memory_root/profile.md``) but a distinct layer:
    user-owned and writeable via ``store.write_profile`` (unlike core, which has
    no general write API — that C-5 docstring guard is for layer one only).
    Five free-text dimensions; the body is always user-blessed. Provenance is
    STRUCTURAL (the write path is tagged), not per-field — AI only proposes via
    a side-channel (→ 050), it never gets the body write path. See milestone-7
    §画哪五维 and slice-047 grill 定案.

    Attributes:
        ability / methodology / expression / goal: the four self-description
            dimensions.
        other: catch-all escape hatch (AI proposals that fit none of the four
            land here; dedup → tidy slice).
        updated: ISO date of the last write (C-3).
        source: nature of the last commit. This is the LOADED value;
            ``write_profile`` re-stamps it from its ``source`` argument
            (authoritative caller intent), so it round-trips only when the
            caller echoes it.
    """

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    updated: str = ""
    # str (not ProfileSource): this is the LOADED value, which a hand-edited
    # frontmatter could populate with anything. It is validated against
    # ProfileSource only at write time (validate_profile). See profile.py,
    # which derives the allowed set from the ProfileSource Literal.
    source: str = "user-edit"


@dataclass(frozen=True)
class Suggestion:
    """One AI-proposed profile addition (slice-050 candidate-queue item).

    The distill agent derives these from session history; they live in
    ``meta/profile-suggestions.json`` as ``pending`` candidates. The user
    accepts (merged into profile.md) or discards them — the agent NEVER gets
    the profile write path (C-1: structural provenance).

    Attributes:
        id: stable identifier (unique within the queue; the distill agent
            assigns it, the job does not rewrite it).
        dimension: which of the five profile dims this proposes to extend.
        body: the proposed text, appended to the dimension's existing body on
            accept (never replaces — C-1).
        sources: provenance pointers (cc_session_id / date) so the suggestion
            is traceable (C-2). Tuple so the value object is immutable.
        date: ISO date of the source session this was derived from.
        status: pending | accepted | discarded.
    """

    id: str
    dimension: ProfileDimension
    body: str
    sources: tuple[str, ...] = ()
    date: str = ""
    status: SuggestionStatus = "pending"


@dataclass(frozen=True)
class Note:
    """A layer-two knowledge note (the reusable-conclusion track).

    Fields ``title``/``tags``/``summary``/``created``/``updated`` are
    wiki-compatible; the rest are memory-only.

    slice-040-a added: ``kind`` (procedural-memory classifier),
    ``verification_reason`` / ``pain_reason`` / ``conflicts_with`` (the agent's
    rationale), ``source_sessions`` / ``content_hash`` (idempotence key).

    slice-041 adds the correction + lifecycle layer (C-7/C-8/C-9):
        memory_id: stable UUIDv7 identity (D1). Survives title/slug edits —
            the filename stem is a human-readable index, NOT the identity.
            The supersedes chain threads ``memory_id`` values, not slugs.
        status: lifecycle (active | contradicted | superseded | retired).
            Replaces the old ``retired:bool`` (C-9 — one axis, no clash).
        supersedes / superseded_by: the correction chain. A new note that
            replaces an old one sets ``supersedes=[old_memory_id]``; the old
            note gets ``superseded_by=<new_memory_id>`` + ``status=superseded``.
        valid_from / last_verified_at: when the conclusion became true / was
            last independently confirmed.
        helpful_refs / harmful_refs: split the old ``refs`` (C-8). Rebuilt
            from outcome-log (C-10 — logs are truth, these are caches).
        trigger / do_not_use_when: scope a procedural note (when to apply /
            when NOT to). Body still carries the four-element procedure.
        sources: multi-source provenance — merge absorbs siblings here.

    ``confidence`` and ``retired:bool`` were REMOVED (C-9, grill 2026-07-11):
    confidence was a derived mirror of verification; retired was a status
    value. Both are rebuilt from verification + status at read time.
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
    # slice-041 correction + lifecycle (C-7/C-8/C-9)
    memory_id: str = ""
    status: NoteStatus = "active"
    supersedes: tuple[str, ...] = ()
    superseded_by: str = ""
    valid_from: str = ""
    last_verified_at: str = ""
    # refs split (C-8); logs are truth, these are rebuildable caches (C-10)
    refs: int = 0
    helpful_refs: int = 0
    harmful_refs: int = 0
    last_ref: str = ""
    # procedural scope
    trigger: str = ""
    do_not_use_when: str = ""
    # multi-source provenance (merge absorbs siblings)
    sources: tuple[str, ...] = ()
    source_sessions: tuple[str, ...] = ()
    content_hash: str = ""
    body: str = ""


@dataclass(frozen=True)
class Diary:
    """An experience-track entry (event stream: time / what / where-stuck / pain).

    Attributes:
        date: ISO date of the entry.
        layer: compression level (day | week | month).
        period: the interval this entry covers (e.g. a week range).
        promoted_knowledge: note ids already lifted out into notes/ (dedupe).
    """

    type: Literal["diary"]
    date: str
    layer: DiaryLayer = "day"
    period: str = ""
    promoted_knowledge: tuple[str, ...] = ()
    body: str = ""


@dataclass(frozen=True)
class DictionaryEntry:
    """A dictionary index node (L0 root overview, or one L1 domain index).

    The body (titles + summaries + triggers) is regenerated by slice-041; here
    we only pin the frontmatter shape.
    """

    type: Literal["dictionary"]
    layer: DictionaryLayer
    domain: str = ""


@dataclass(frozen=True)
class PersistContext:
    """Provenance for landing one distilled session (slice-040-a).

    Built by ``run_daily_review`` from the SessionRecord and passed alongside
    the Draft to ``persist_draft`` — persist MUST NOT guess its source. One
    source session maps to exactly one episode file (``episodes/<cc_session_id>
    .md``) but may grow multiple incremental segments across re-runs; 040-a
    uses a single whole-file segment ``<cc_session_id>:0:end``, 040-b reuses
    the same contract with incremental offsets.

    Attributes:
        segment_id: stable segment key (``<cc_session_id>:<start>:<end>``).
        cc_session_id: cc's uuid session id (the jsonl filename stem).
        workdir: the session's working directory.
        registered_at: ISO timestamp the session was registered.
        review_date: ISO ``YYYY-MM-DD`` the review runs for (= session.date).
        source_jsonl: absolute path to the cc session jsonl.
        source_start_offset: byte offset the segment starts at (0 for 040-a).
        source_end_offset: byte offset the segment ends at (None = EOF).
    """

    segment_id: str
    cc_session_id: str
    workdir: str
    registered_at: str
    review_date: str
    source_jsonl: str
    source_start_offset: int = 0
    source_end_offset: int | None = None
    # slice-061: the real calendar dates this segment's events fall on, + how
    # they were derived. daily projection reads per-segment activity_dates
    # (block-4) instead of the top-level review_date, so a re-run / resume never
    # re-files old content under today. ``activity_dates`` is empty when the
    # segment had no attributable day (block-3 extraction returned nothing).
    activity_dates: tuple[str, ...] = ()
    date_basis: str = ""
    processed_date: str = ""
