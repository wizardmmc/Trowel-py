"""TidyPlan: LLM produces, Python validates + applies atomically (slice-041).

The tidy LLM only EMITS a plan (merge/supersede/contradict/retire/keep/revise
operations) — it never rewrites notes directly. Python validates the
invariants (targets exist, no supersedes cycle, sources not lost), checks
staleness against the plan's source snapshot, backs up ``notes/`` to
``meta/snapshots/<plan_id>/``, and applies atomically. ``rollback_plan``
restores from the snapshot (C-12).

``run_weekly_tidy`` orchestrates one ISO week: recompute counters → compress
the week (+ bypass) → build a tidy plan from this week's notes + conflicts →
apply. The LLM prompt that PRODUCES a plan lives here; the monthly orchestration
(T9) reuses ``build_tidy_plan`` with a month-scoped note set.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:  # Unix-only; Windows has no flock → the lock becomes a no-op there.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.promotion_policy import PromotionPolicy, default_policy
from trowel_py.memory.store import MemoryStore, _dump_frontmatter

logger = logging.getLogger(__name__)


def _ensure_dictionary(root: Path | str, provider: LLMProvider) -> dict[str, Any]:
    """slice-064 §4: converge the dictionary after a tidy batch; surface status.

    tidy mutates notes (retire/supersede/merge/revise), so the index can drift.
    This runs the read-only check and full-rebuilds if stale, returning
    ``dictionary_status`` for the tidy report. Never raises — a rebuild failure
    marks the state stale (C-7) and is retried on the next run.
    """
    from trowel_py.memory.dictionary import ensure_dictionary_consistent

    try:
        return ensure_dictionary_consistent(root, provider)
    except Exception as exc:  # noqa: BLE001 — isolate; tidy already succeeded
        logger.warning("dictionary ensure failed after tidy: %s", exc)
        return {"dictionary_status": "stale", "error": str(exc)}

OpType = Literal["merge_sources", "revise", "supersede", "contradict", "retire", "keep"]

_SNAPSHOTS_DIR = "meta/snapshots"
_VALID_OP_TYPES = {
    "merge_sources", "revise", "supersede", "contradict", "retire", "keep",
}
#: slice-041 retirement + promotion thresholds (grill 2026-07-11).
HALF_LIFE_DAYS = 90
HARMFUL_RETIRE_THRESHOLD = 3
_CANDIDATES_DIR = "meta/core-candidates"
#: C2 (codex): fields a revise op may change. Identity (memory_id/type/
#: content_hash/kind/title), lifecycle (status/superseded_by/supersedes) and
#: counters (refs/helpful_refs/harmful_refs/last_ref) are off-limits — they
#: move via dedicated ops (retire/supersede/contradict) or are owned by
#: record_ref/recompute. body is excluded: rewriting it would invalidate
#: content_hash (the stale key) without rehashing; a changed conclusion
#: should go through supersede, not revise.
_REVISE_ALLOWED_FIELDS = frozenset({
    "summary", "verification", "verification_reason", "pain", "pain_reason",
    "trigger", "do_not_use_when", "valid_from", "last_verified_at",
    "tags", "sources", "conflicts_with",
})


def _validate_revise_op(
    root: Path, op: TidyOperation, id_map: dict[str, str]
) -> list[str]:
    """C2 (codex): whitelist + schema-check a revise op's ``new_fields``.

    The LLM emits ``new_fields`` freely; without a guard it could rewrite
    ``memory_id`` (breaking the stable identity + supersedes chain) or set an
    invalid ``status``. Reject any field outside the allowlist, then
    schema-validate the simulated post-revise frontmatter so enum/type rules
    still bind on allowlisted fields (e.g. ``verification`` must be valid).
    """
    errs: list[str] = []
    bad = sorted(set(op.new_fields) - _REVISE_ALLOWED_FIELDS)
    if bad:
        errs.append(
            f"op revise: field(s) {bad} not in allowlist; revise may only set "
            f"{sorted(_REVISE_ALLOWED_FIELDS)} (C-2)"
        )
        return errs
    stem = id_map.get(op.target)
    if not stem:
        return errs  # target-missing is reported by the upstream existence check
    from trowel_py.memory.schema import validate_entry
    from trowel_py.memory.store import _split_frontmatter

    path = root / "notes" / f"{stem}.md"
    fm, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
    simulated = dict(fm or {})
    simulated.update(op.new_fields)
    vr = validate_entry("note", simulated)
    if not vr.ok:
        errs.append(f"op revise {op.target}: schema reject: {vr.errors}")
    return errs


_TIDY_SYS = (
    "你是记忆整理器。读本周新笔记 + 现有笔记索引 + 冲突的旧笔记，产出整理计划。"
    "operation 类型：merge_sources（同主题合并，target 合入 canonical）/ supersede（新结论取代旧，target 被 by 取代）/ "
    "contradict（旧结论被证伪）/ retire（过时退场）/ keep（保留不动）。"
    "每项带 reason。target/canonical/by 都填 memory_id（只从给定的里选）。"
    '输出 JSON: {"operations":[{"type":"...","target":"<mid>","reason":"...","canonical":"<mid>","by":"<mid>"}]}。'
    "只输出 JSON，不要解释。若无操作，输出 {\"operations\":[]}。"
)


@dataclass(frozen=True)
class TidyOperation:
    """One planned mutation on a note.

    Attributes:
        type: the mutation kind.
        target: the memory_id being operated on.
        reason: why (LLM's rationale, shown in the report).
        evidence: source session/memory ids supporting this op.
        expected_revision: the content_hash the plan was built against; apply
            refuses if the note changed since (stale guard, C-12).
        canonical: for merge_sources — the id target merges INTO.
        by: for supersede/contradict — the id that replaces/refutes target.
        new_fields: for revise — frontmatter fields to set.
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
    """A validated-then-applied tidy plan.

    Attributes:
        plan_id: stable id (names the snapshot dir).
        source_snapshot: ``{memory_id: content_hash}`` at plan-build time.
        operations: the mutations to apply.
        dictionary_rebuild_required: hint to rebuild the dictionary after apply.
        core_candidates: memory_ids flagged for layer-one promotion (monthly).
    """

    plan_id: str
    source_snapshot: dict[str, str]
    operations: tuple[TidyOperation, ...]
    dictionary_rebuild_required: bool = False
    core_candidates: tuple[str, ...] = ()


def _memory_id_to_stem(root: Path) -> dict[str, str]:
    """Build ``{memory_id: filename_stem}`` so ops (keyed by memory_id) can
    reach the file (keyed by stem). Notes without memory_id are skipped — tidy
    assumes ``trowel memory migrate`` has already run."""
    store = MemoryStore(root)
    return {
        n.memory_id: stem
        for stem, n in store.load_notes_with_id()
        if n.memory_id
    }


def validate_plan(root: Path, plan: TidyPlan) -> list[str]:
    """Return validation errors (empty list = plan is sound).

    Checks: every target/canonical/by exists; no self-replacement; the
    supersedes/contradict/merge graph — existing persisted ``superseded_by``
    edges overlaid with this plan's new edges — has no cycle (C-7 — a note
    must not replace itself transitively, even via an already-persisted chain).
    """
    errors: list[str] = []
    id_map = _memory_id_to_stem(root)
    for op in plan.operations:
        if op.target not in id_map:
            errors.append(f"op {op.type}: target {op.target!r} not found in notes")
        if op.type == "merge_sources" and op.canonical not in id_map:
            errors.append(f"op merge_sources: canonical {op.canonical!r} not found")
        if op.type in ("supersede", "contradict") and op.by not in id_map:
            errors.append(f"op {op.type}: by {op.by!r} not found")
        # C1 (codex): reject self-replacement (target == by/canonical)
        replacer = op.by or op.canonical
        if replacer and op.target == replacer:
            errors.append(
                f"op {op.type}: target {op.target!r} cannot replace itself (自指)"
            )
        # C2 (codex): revise new_fields must be allowlisted + schema-valid
        if op.type == "revise":
            errors.extend(_validate_revise_op(root, op, id_map))
    # C1 (codex): cycle check on the FULL graph — start from persisted
    # superseded_by edges, then overlay this plan's new edges. A plan that
    # closes a cycle with an already-persisted correction chain must be
    # rejected (C-7); checking only the plan's own edges misses A->B (saved)
    # + new B->A.
    store = MemoryStore(root)
    edges: dict[str, str] = {}
    for _stem, n in store.load_notes_with_id():
        if n.memory_id and n.superseded_by:
            edges[n.memory_id] = n.superseded_by
    for op in plan.operations:
        if op.type in ("supersede", "contradict", "merge_sources"):
            replacer = op.by or op.canonical
            if replacer:
                edges[op.target] = replacer
    if _has_cycle(edges):
        errors.append("supersede/contradict/merge chain has a cycle (订正链成环，含已有订正链)")
    return errors


def _has_cycle(edges: dict[str, str]) -> bool:
    """True if the target→replacer graph reaches a node already on its path."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {n: WHITE for n in set(edges) | set(edges.values())}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        nxt = edges.get(node)
        if nxt is not None:
            c = color.get(nxt, WHITE)
            if c == GRAY:
                return True
            if c == WHITE and dfs(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in list(color))


@contextlib.contextmanager
def _tidy_lock(root: Path):
    """C-12 mutex: exclusive flock so concurrent tidy runs skip (slice-041).

    A manual ``trowel memory tidy`` and a scheduled run could race on the
    recompute → snapshot → apply sequence; the second caller takes
    ``BlockingIOError``, which ``run_weekly_tidy``/``run_monthly_tidy`` catch
    and report as skipped. Off-Unix (``fcntl`` is None) the lock is a no-op.
    """
    if fcntl is None:
        yield
        return
    lock_path = root / "meta" / ".tidy.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def apply_plan(root: Path | str, plan: TidyPlan) -> dict[str, Any]:
    """Validate + back up + apply atomically. Raises ValueError on stale/invalid.

    Returns a report dict written to ``meta/snapshots/<plan_id>/report.json``.
    """
    root_path = Path(root)
    errors = validate_plan(root_path, plan)
    if errors:
        raise ValueError(f"invalid plan: {errors}")

    store = MemoryStore(root_path)
    id_map = _memory_id_to_stem(root_path)

    # stale guard: every target's current content_hash must match the plan's
    # snapshot (or the op's expected_revision if pin-pointed).
    for op in plan.operations:
        stem = id_map[op.target]
        note = store.load_note(stem)
        if note is None:
            raise ValueError(f"stale: target {op.target} vanished before apply")
        expected = op.expected_revision or plan.source_snapshot.get(op.target)
        if expected and note.content_hash != expected:
            raise ValueError(
                f"stale: {op.target} changed (expected {expected}, "
                f"got {note.content_hash})"
            )

    # backup notes/ + write plan.json (the snapshot for rollback)
    snap_dir = root_path / _SNAPSHOTS_DIR / plan.plan_id
    snap_dir.parent.mkdir(parents=True, exist_ok=True)
    notes_src = root_path / "notes"
    snap_notes = snap_dir / "notes"
    if snap_notes.exists():
        # W3 (codex): same-period rerun — replace the old snapshot's notes
        # cleanly instead of letting copytree raise FileExistsError.
        shutil.rmtree(snap_notes)
    if notes_src.exists():
        shutil.copytree(notes_src, snap_notes)
    (snap_dir / "plan.json").write_text(
        json.dumps(_plan_to_dict(plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    applied: list[str] = []
    try:
        for op in plan.operations:
            stem = id_map[op.target]
            if op.type == "keep":
                applied.append(op.target)
                continue
            if op.type == "retire":
                store.update_note_fields(stem, {"status": "retired"})
            elif op.type == "revise":
                # C2 (codex): defense-in-depth — validate_plan already rejected
                # non-allowlisted fields, but filter at apply time too so a
                # caller that bypasses validate cannot write identity fields.
                safe = {k: v for k, v in op.new_fields.items()
                        if k in _REVISE_ALLOWED_FIELDS}
                store.update_note_fields(stem, safe)
            elif op.type in ("supersede", "contradict"):
                store.update_note_fields(stem, {
                    "status": "superseded" if op.type == "supersede" else "contradicted",
                    "superseded_by": op.by,
                })
                # the replacer gains supersedes=[target] (C-7 chain)
                replacer_stem = id_map[op.by]
                replacer = store.load_note(replacer_stem)
                if replacer is not None:
                    new_super = tuple(sorted(set(replacer.supersedes) | {op.target}))
                    store.update_note_fields(replacer_stem, {"supersedes": new_super})
            elif op.type == "merge_sources":
                store.update_note_fields(stem, {
                    "status": "superseded", "superseded_by": op.canonical,
                })
                canon_stem = id_map[op.canonical]
                canon = store.load_note(canon_stem)
                target = store.load_note(stem)
                # C-12: canonical absorbs target's sources — none lost
                if canon is not None and target is not None:
                    merged = tuple(sorted(set(canon.sources) | set(target.sources)))
                    store.update_note_fields(canon_stem, {"sources": merged})
            applied.append(op.target)
    except Exception:
        # W1 (auto-cr): apply is not physically atomic across files — if an op
        # fails mid-way, restore notes/ from the snapshot so no half-applied
        #订正链 survives. The snapshot dir stays for inspection.
        notes_dst = root_path / "notes"
        if (snap_dir / "notes").exists():
            if notes_dst.exists():
                shutil.rmtree(notes_dst)
            shutil.copytree(snap_dir / "notes", notes_dst)
        raise

    report = {
        "plan_id": plan.plan_id,
        "applied": applied,
        "operations": len(plan.operations),
    }
    (snap_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def rollback_plan(root: Path | str, plan_id: str) -> None:
    """Restore ``notes/`` from the snapshot taken at apply time (C-12).

    W2 (auto-cr): atomic rename — move the current notes/ aside first, copy
    the backup into place, then delete the trash. If copytree fails mid-way,
    the trash is renamed back so notes/ is never left missing.
    """
    snap_dir = Path(root) / _SNAPSHOTS_DIR / plan_id
    notes_backup = snap_dir / "notes"
    if not notes_backup.exists():
        raise FileNotFoundError(f"no snapshot for plan {plan_id!r}")
    notes_dst = Path(root) / "notes"
    trash = notes_dst.with_name(notes_dst.name + ".trash")
    if notes_dst.exists():
        notes_dst.rename(trash)  # atomic move aside
    try:
        shutil.copytree(notes_backup, notes_dst)
    except Exception:
        if notes_dst.exists():
            shutil.rmtree(notes_dst)
        if trash.exists():
            trash.rename(notes_dst)  # restore the pre-rollback state
        raise
    if trash.exists():
        shutil.rmtree(trash)


def _plan_to_dict(plan: TidyPlan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "source_snapshot": plan.source_snapshot,
        "operations": [
            {
                "type": op.type, "target": op.target, "reason": op.reason,
                "evidence": list(op.evidence),
                "expected_revision": op.expected_revision,
                "canonical": op.canonical, "by": op.by,
                "new_fields": op.new_fields,
            }
            for op in plan.operations
        ],
        "dictionary_rebuild_required": plan.dictionary_rebuild_required,
        "core_candidates": list(plan.core_candidates),
    }


# ----------------------------------------------------------- weekly tidy (T8)


def _note_in_iso_week(date_str: str, iso_year: int, iso_week: int) -> bool:
    """True if an ISO date string falls in the given ISO week."""
    from trowel_py.memory.compress import _in_iso_week

    return _in_iso_week(date_str, iso_year, iso_week)


def build_tidy_plan(
    root: Path | str, iso_week: str, provider: LLMProvider,
    *, plan_id: str | None = None,
) -> TidyPlan:
    """LLM produces a TidyPlan from this week's notes + conflicts + L1 index.

    Feeds the LLM: this week's new/updated notes (full body), the dictionary
    L1 index (titles+summaries, to spot cross-week duplicates), and the old
    notes this week's notes ``conflicts_with`` (correction candidates). No
    LLM call when the week has no notes.
    """
    from trowel_py.memory.compress import _parse_iso_week

    iso_year, iso_week_num = _parse_iso_week(iso_week)

    def in_scope(date_str: str) -> bool:
        return _note_in_iso_week(date_str, iso_year, iso_week_num)

    return _build_plan_for_scope(
        root, plan_id or f"weekly-{iso_week}", provider, in_scope
    )


def build_monthly_plan(
    root: Path | str, month: str, provider: LLMProvider,
    *, plan_id: str | None = None,
) -> TidyPlan:
    """LLM produces a TidyPlan from this month's notes (monthly tidy, T9)."""
    def in_scope(date_str: str) -> bool:
        return bool(date_str) and date_str.startswith(month)

    return _build_plan_for_scope(
        root, plan_id or f"monthly-{month}", provider, in_scope
    )


def _build_plan_for_scope(
    root: Path | str, plan_id: str, provider: LLMProvider,
    in_scope: Any,
) -> TidyPlan:
    """Shared LLM→plan builder. ``in_scope(date_str)`` picks this period's
    notes (by created/updated). Feeds scope notes + L1 index + conflicts to
    the LLM, parses the operations, returns a TidyPlan (no apply)."""
    root_path = Path(root)
    store = MemoryStore(root_path)
    all_with_id = store.load_notes_with_id()
    scope_notes = [
        (s, n) for s, n in all_with_id
        if n.memory_id and (in_scope(n.created) or in_scope(n.updated))
    ]
    snapshot = {n.memory_id: n.content_hash for _s, n in all_with_id if n.memory_id}
    if not scope_notes:
        return TidyPlan(plan_id=plan_id, source_snapshot=snapshot, operations=())

    # conflicts_with points at stems (040-a); resolve to (stem, note) pairs.
    conflict_stems = {c for _s, n in scope_notes for c in n.conflicts_with}
    conflict_notes = [(s, n) for s, n in all_with_id if s in conflict_stems]

    l1_dir = root_path / "dictionary-L1"
    l1_text = ""
    if l1_dir.exists():
        l1_text = "\n\n".join(
            p.read_text(encoding="utf-8") for p in sorted(l1_dir.glob("*.md"))
        )

    def _block(n: Any) -> str:
        return f"[{n.memory_id}] {n.title} — {n.summary}\nbody: {n.body[:500]}"

    scope_block = "\n\n".join(_block(n) for _s, n in scope_notes)
    conflict_block = "\n\n".join(_block(n) for _s, n in conflict_notes)
    user = (
        f"本期新/改笔记：\n{scope_block}\n\n"
        f"现有笔记索引（L1，发现跨期重复用）：\n{l1_text}\n\n"
        f"冲突的旧笔记（订正候选）：\n{conflict_block}\n\n"
        "输出 operations JSON。"
    )
    raw = provider.complete(_TIDY_SYS, user)
    ops = _parse_operations(raw)
    # Drop ops whose target/by/canonical references a memory_id the LLM
    # hallucinated (not in the known set). Better to apply the valid subset
    # than to reject the whole plan on one hallucinated id — the LLM is
    # unreliable at echoing UUIDs verbatim.
    known_ids = set(snapshot)
    valid_ops: list[TidyOperation] = []
    for op in ops:
        if op.target not in known_ids:
            continue
        if op.type == "merge_sources" and op.canonical not in known_ids:
            continue
        if op.type in ("supersede", "contradict") and op.by not in known_ids:
            continue
        valid_ops.append(op)
    return TidyPlan(plan_id=plan_id, source_snapshot=snapshot, operations=tuple(valid_ops))


def _parse_operations(raw: str) -> tuple[TidyOperation, ...]:
    """Parse the LLM's ``{"operations": [...]}`` JSON into TidyOperations.

    Drops any op whose type is unknown or target is empty. Non-JSON → empty.
    """
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return ()
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return ()
    out: list[TidyOperation] = []
    for op in data.get("operations", []):
        if not isinstance(op, dict):
            continue
        t = op.get("type")
        if t not in _VALID_OP_TYPES:
            continue
        target = str(op.get("target", "")).strip()
        if not target:
            continue
        out.append(TidyOperation(
            type=t,  # type: ignore[arg-type]
            target=target,
            reason=str(op.get("reason", "")),
            canonical=str(op.get("canonical", "")),
            by=str(op.get("by", "")),
            new_fields=dict(op.get("new_fields", {})) if t == "revise" else {},
        ))
    return tuple(out)


def run_weekly_tidy(
    root: Path | str, iso_week: str, provider: LLMProvider
) -> dict[str, Any]:
    """One ISO week of tidy: recompute → compress (+bypass) → plan → apply.

    All steps are non-fatal individually: a compress failure leaves the old
    daily/weekly; a plan with no operations skips apply. Returns a report.
    """
    from trowel_py.memory.compress import compress_weekly
    from trowel_py.memory.recompute import recompute_counters

    root_path = Path(root)
    try:
        with _tidy_lock(root_path):
            recompute_counters(root_path)
            compress_report = compress_weekly(root_path, iso_week, provider)
            plan = build_tidy_plan(root_path, iso_week, provider)
            if plan.operations:
                try:
                    tidy_report = apply_plan(root_path, plan)
                except Exception as exc:  # noqa: BLE001 — apply failure still converges dict
                    tidy_report = {"plan_id": plan.plan_id, "error": str(exc),
                                   "applied": [], "operations": len(plan.operations)}
            else:
                tidy_report = {"plan_id": plan.plan_id, "applied": [],
                               "operations": 0}
            # slice-064 F7: ALWAYS converge the dictionary — whether or not this
            # run applied ops, a prior failed run may have left the index stale,
            # and a partial apply may have changed notes. Rebuild on stale.
            dict_report = _ensure_dictionary(root_path, provider)
    except BlockingIOError:
        return {"plan_id": f"weekly-{iso_week}", "skipped": "another tidy is running"}
    return {
        "plan_id": plan.plan_id,
        "compress": compress_report,
        "tidy": tidy_report,
        "dictionary": dict_report,
    }


# --------------------------------------------------------- monthly tidy (T9)


def plan_retirements(root: Path | str, today_str: str) -> tuple[TidyOperation, ...]:
    """Python-computed retire operations (no LLM). C-8/D3: 90-day half-life
    on ``last_ref`` (last_ref empty → skip, protects legacy notes whose refs
    were never tracked); ``harmful_refs≥3`` → retire even without half-life.
    contradict/supersede are left to the LLM plan (they need a ``by``).
    """
    from datetime import date as _date, timedelta

    today = _date.fromisoformat(today_str)
    cutoff = today - timedelta(days=HALF_LIFE_DAYS)
    store = MemoryStore(root)
    ops: list[TidyOperation] = []
    for _stem, n in store.load_notes_with_id():
        if n.status != "active" or not n.memory_id:
            continue
        retire = False
        reason = ""
        if n.last_ref:
            try:
                last = _date.fromisoformat(n.last_ref)
                if last < cutoff:
                    retire = True
                    reason = f"未使用 {HALF_LIFE_DAYS}+ 天（last_ref={n.last_ref}）"
            except ValueError:
                pass
        if n.harmful_refs >= HARMFUL_RETIRE_THRESHOLD:
            retire = True
            reason = (reason + "; " if reason else "") + (
                f"harmful_refs={n.harmful_refs}≥{HARMFUL_RETIRE_THRESHOLD}"
            )
        if retire:
            ops.append(TidyOperation(type="retire", target=n.memory_id, reason=reason))
    return tuple(ops)


def promote_candidates(
    root: Path | str,
    *,
    policy: PromotionPolicy | None = None,
    local_tz: Any | None = None,
    today: str | None = None,
) -> list[str]:
    """Write/refresh candidate files for notes that clear ``policy``
    (slice-065 — candidate file only, NEVER core.md). Delegates to
    ``evaluate_promotion`` so the gate is the same policy the metrics and the
    monthly report carry. Returns the promoted memory_ids.
    """
    from trowel_py.memory.promotion import evaluate_promotion

    report = evaluate_promotion(
        root, policy or default_policy(), local_tz=local_tz, today=today
    )
    return list(report["candidates"])


def _write_candidate(root: Path, note: Any) -> Path:
    """Write a candidate file for a hand-nominated note (``core_ops.nominate`` —
    below the policy threshold). Distinct from ``promotion._write_candidate``,
    which carries the full session-level evidence; this one records only what
    the note already caches plus a ``manual-nominate`` provenance stamp."""
    path = root / _CANDIDATES_DIR / f"{note.memory_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {
        "type": "core-candidate", "memory_id": note.memory_id,
        "source_title": note.title, "helpful_refs": note.helpful_refs,
        "kind": note.kind, "verification": note.verification,
        "policy_version": "manual-nominate", "status": "candidate",
    }
    body = (
        f"# 候选：{note.title}\n\n{note.summary}\n\n## 正文\n\n{note.body}\n\n"
        "## 晋升理由\n\n人工提名（helpful 证据未达自动策略阈值）。"
    )
    path.write_text(_dump_frontmatter(fm, body), encoding="utf-8")
    return path


def run_monthly_tidy(
    root: Path | str, month: str, provider: LLMProvider, *, today: str | None = None,
) -> dict[str, Any]:
    """One month of tidy: recompute → retire (Python) → promote (Python) →
    compress monthly (LLM) → build plan (LLM) + merge retire ops → apply.

    Retirement + promotion are Python-computed (no LLM); the LLM only
    produces merge/supersede/contradict ops + the monthly compression. The
    two op sets merge into one TidyPlan applied atomically (C-12).
    """
    from datetime import date as _date

    from trowel_py.memory.compress import compress_monthly
    from trowel_py.memory.recompute import recompute_counters

    root_path = Path(root)
    today_str = today or _date.today().isoformat()
    try:
        with _tidy_lock(root_path):
            recompute_report = recompute_counters(root_path)
            retire_ops = plan_retirements(root_path, today_str)
            from trowel_py.memory.promotion import evaluate_promotion
            promotion_report = evaluate_promotion(
                root_path, default_policy(), today=today_str
            )
            promoted = promotion_report["candidates"]
            compress_report = compress_monthly(root_path, month, provider)
            plan = build_monthly_plan(root_path, month, provider)
            merged_plan = TidyPlan(
                plan_id=plan.plan_id,
                source_snapshot=plan.source_snapshot,
                operations=retire_ops + plan.operations,
                core_candidates=tuple(promoted),
            )
            if merged_plan.operations:
                try:
                    tidy_report = apply_plan(root_path, merged_plan)
                except Exception as exc:  # noqa: BLE001 — apply failure still converges dict
                    tidy_report = {
                        "plan_id": merged_plan.plan_id, "error": str(exc),
                        "applied": [], "operations": len(merged_plan.operations),
                    }
            else:
                tidy_report = {"plan_id": merged_plan.plan_id, "applied": [],
                               "operations": 0}
            # slice-064 F7: ALWAYS converge (see run_weekly_tidy).
            dict_report = _ensure_dictionary(root_path, provider)
    except BlockingIOError:
        return {"plan_id": f"monthly-{month}", "skipped": "another tidy is running"}
    return {
        "plan_id": merged_plan.plan_id, "compress": compress_report,
        "tidy": tidy_report, "recompute": recompute_report,
        "promotion": promotion_report, "promoted": promoted,
        "retire_ops": len(retire_ops),
        "dictionary": dict_report,
    }
