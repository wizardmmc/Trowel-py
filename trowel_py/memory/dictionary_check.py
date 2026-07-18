"""dictionary consistency check + active-corpus derivation (slice-064 §1/§2).

Notes are the single source of truth (C-1); the L0/L1 index is a derived view.
This module is the read-only bridge that proves (or disproves) the on-disk
index still matches the note facts. It never calls an LLM and never writes
(C-6) — ``check_dictionary`` is the diagnostic the CLI, review_job, and tidy
call after mutations, and the gate a full rebuild must pass on staging before
it is allowed to replace the live index.

The active corpus + its ``source_hash`` are the canonical "what the index
should currently contain". The hash covers exactly the note fields the L1
render depends on (stem/title/summary/tags/status), normalized to sorted
tuples so file order and mtime do not affect it (slice-064 §2).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from trowel_py.memory.dictionary_lock import dictionary_lock
from trowel_py.memory.dictionary_state import load_state
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

# Fields of the report that carry per-stem lists (used by summarize helpers).
_REPORT_KEYS = (
    "missing_active", "inactive_indexed", "missing_l1_files",
    "orphan_l1_files",
)

_DICT_L0 = "dictionary-L0.md"
_DICT_L1_DIR = "dictionary-L1"

# A rendered L1 entry carries a content-independent stem anchor so the check
# can round-trip the stem even when the stem itself contains backticks (a
# backtick inside `` `notes/{stem}.md` `` would close the code span early and
# break the legacy extractor). New entries append ``<!-- @stem {stem} -->``;
# the legacy backtick extractor is kept as a fallback for pre-anchor files.
_L1_STEM_ANCHOR_RE = re.compile(r"<!-- @stem (\S+) -->")
_L1_STEM_RE = re.compile(r"`notes/([^`]+)\.md`")
# A rendered L0 domain header looks like:
#   ### {name}（{count} 条 → read dictionary-L1/{name}.md）
_L0_DOMAIN_RE = re.compile(r"^### (\S+?)（(\d+) 条")


def derive_active_corpus(root: Path | str) -> list[tuple[str, Note]]:
    """Return ``[(stem, Note)]`` for active notes only (C-3).

    superseded / contradicted / retired are the note facts but never enter the
    default index, so the corpus the index is derived from is active-only.
    """
    store = MemoryStore(root)
    return store.load_notes_with_id({"status": "active"})


def compute_source_hash(corpus: list[tuple[str, Note]]) -> str:
    """Stable hash of the active corpus (slice-064 §2).

    Covers the note fields the L1 render depends on (stem/title/summary/tags/
    status), normalized to sorted tuples so iteration order and file mtime do
    not affect it. A change a searcher can see flips the hash; re-deriving in
    a different order does not.
    """
    rows: list[str] = []
    for stem, note in sorted(corpus, key=lambda x: x[0]):
        tags = ",".join(sorted(note.tags))
        rows.append(f"{stem}\t{note.title}\t{note.summary}\t{tags}\t{note.status}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def compute_rendered_hash(l0_text: str, l1_files: dict[str, str]) -> str:
    """Stable hash of the on-disk index content (L0 + all L1, sorted by domain).

    Captures a hand-edit of any rendered field (title/summary/triggers) that
    leaves stems + counts unchanged — ``source_hash`` (over note facts) would
    miss it, but this catches the published index diverging from what the last
    rebuild rendered (slice-064 F4). Domain order does not affect the hash.
    """
    parts = [l0_text] + [l1_files[k] for k in sorted(l1_files)]
    return hashlib.sha256("\n\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _parse_l0(l0_text: str) -> list[tuple[str, int]]:
    """Return ``[(domain, declared_count)]`` from L0 headers."""
    out: list[tuple[str, int]] = []
    for line in l0_text.splitlines():
        m = _L0_DOMAIN_RE.match(line)
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def _parse_l1_stems(l1_text: str) -> list[str]:
    """Stems referenced by one L1 file, in document order (dups preserved).

    Prefers the content-independent ``<!-- @stem ... -->`` anchor (robust to
    stems that contain backticks); falls back to the legacy backtick extractor
    for L1 files rendered before the anchor existed.
    """
    anchored = _L1_STEM_ANCHOR_RE.findall(l1_text)
    if anchored:
        return anchored
    return _L1_STEM_RE.findall(l1_text)


def _evaluate(
    corpus: list[tuple[str, Note]],
    l0_text: str | None,
    l1_files: dict[str, str],
    state_hash: str | None,
    *,
    state_status: str = "consistent",
    state_rendered_hash: str | None = None,
    baseline_required: bool = True,
) -> dict[str, Any]:
    """Pure comparison of an index against the active corpus (no filesystem).

    ``l0_text=None`` means "no index" → ``status=missing``. ``l1_files`` maps
    domain → L1 body text. ``state_hash`` is the last-success corpus hash from
    the state file.

    ``state_status`` (consistent|stale|missing) is AUTHORITATIVE for trust
    (slice-064 C-7): a stale/missing state must never be certified consistent,
    even if structure + hash happen to match — a rebuild that failed (or never
    finished) must stay observable + retryable. The staging check inside a
    rebuild passes the default ``consistent`` because it evaluates a freshly
    rendered set, not the live trusted state.

    ``baseline_required``: how a missing hash baseline (``state_hash is None``)
    is treated. The live ``check_dictionary`` path requires a baseline — no
    baseline means the index is unverified, so a content-only drift can't be
    masked (slice-064 H-1). The staging check inside a rebuild passes
    ``baseline_required=False`` because the rendered set carries no baseline
    yet (it is about to become one); there a missing baseline must NOT flag
    stale, only a structural disagreement should.
    """
    active_stems = {stem for stem, _ in corpus}
    cur_hash = compute_source_hash(corpus)

    if l0_text is None:
        # No L0 root, but still report what the on-disk L1 files carry so the
        # operator sees the gap (slice-064 F12): every active note is missing
        # from the usable index, every L1 file is an orphan with no L0 to
        # declare it. declared-count / duplicate tracking need an L0, so empty.
        indexed: set[str] = set()
        for text in l1_files.values():
            indexed |= set(_parse_l1_stems(text))
        return {
            "status": "missing",
            "active_notes": len(active_stems),
            "indexed_unique": len(indexed),
            "missing_active": sorted(active_stems),
            "inactive_indexed": sorted(indexed - active_stems),
            "duplicate_entries": [],
            "missing_l1_files": [],
            "orphan_l1_files": sorted(l1_files),
            "l0_count_mismatches": [],
            "source_hash_matches": None,
            "rendered_hash_matches": None,
        }

    declared = _parse_l0(l0_text)
    declared_set = {name for name, _ in declared}

    # stem -> domains that reference it; domain -> actual entry count
    stem_domains: dict[str, list[str]] = {}
    domain_actual: dict[str, int] = {}
    on_disk_l1: set[str] = set()
    for domain, text in l1_files.items():
        on_disk_l1.add(domain)
        stems = _parse_l1_stems(text)
        domain_actual[domain] = len(stems)
        for stem in stems:
            stem_domains.setdefault(stem, []).append(domain)

    indexed_stems = set(stem_domains)

    # missing_active: active notes the index does not surface
    missing_active = sorted(active_stems - indexed_stems)
    # inactive_indexed: indexed stems that are NOT active notes — whether the
    # note is superseded/contradicted/retired or simply gone, it must not be in
    # the default index (C-3). (indexed - active) captures both without a
    # per-stem store lookup, so this stays a pure function of (corpus, index).
    inactive_indexed = sorted(indexed_stems - active_stems)
    # duplicate_entries: one stem referenced more than once
    duplicate_entries = sorted(
        (
            {"stem": stem, "count": len(doms), "domains": sorted(set(doms))}
            for stem, doms in stem_domains.items()
            if len(doms) > 1
        ),
        key=lambda d: str(d["stem"]),
    )
    missing_l1_files = sorted(declared_set - on_disk_l1)
    orphan_l1_files = sorted(on_disk_l1 - declared_set)
    l0_count_mismatches = [
        {"domain": name, "declared": declared_count,
         "actual": domain_actual.get(name, 0)}
        for name, declared_count in declared
        if declared_count != domain_actual.get(name, 0)
    ]

    # No baseline (no state yet, or state lost) → do NOT claim the hash agrees.
    # A missing baseline must not mask a content-only drift (a title/summary/
    # tags change that leaves stems + counts unchanged); the index is treated
    # as unverified until a rebuild stamps the first hash (slice-064 H-1).
    source_hash_matches = state_hash is not None and state_hash == cur_hash
    # check path: no baseline (or a mismatched one) is dirty. staging path:
    # the rendered set has no baseline yet, so only a structural disagreement
    # counts — a missing baseline must not block publishing a fresh index.
    hash_dirty = (not source_hash_matches) if baseline_required else False
    # state status is authoritative for trust (C-7): a stale/missing state must
    # stay stale even when structure + hash match, so a failed/unfinished
    # rebuild is always retried and never silently served as consistent.
    state_untrusted = state_status != "consistent"
    # rendered_hash: catches a hand-edit of L0/L1 content that leaves stems +
    # counts (and even the note-fact source_hash) unchanged (slice-064 F4).
    rendered_matches = (
        state_rendered_hash is None
        or state_rendered_hash == compute_rendered_hash(l0_text, l1_files)
    )
    rendered_dirty = (not rendered_matches) if baseline_required else False

    dirty = bool(
        missing_active
        or inactive_indexed
        or duplicate_entries
        or missing_l1_files
        or orphan_l1_files
        or l0_count_mismatches
        or hash_dirty
        or state_untrusted
        or rendered_dirty
    )
    return {
        "status": "stale" if dirty else "consistent",
        "active_notes": len(active_stems),
        "indexed_unique": len(indexed_stems),
        "missing_active": missing_active,
        "inactive_indexed": inactive_indexed,
        "duplicate_entries": duplicate_entries,
        "missing_l1_files": missing_l1_files,
        "orphan_l1_files": orphan_l1_files,
        "l0_count_mismatches": l0_count_mismatches,
        "source_hash_matches": source_hash_matches,
        "rendered_hash_matches": rendered_matches,
    }


def check_dictionary(root: Path | str) -> dict[str, Any]:
    """Read-only consistency report, under a shared dictionary lock (C-2: don't
    read a half-swapped index). Never calls an LLM, never writes (C-6).

    Returns the slice-064 §2 report. ``status`` is ``missing`` when no L0
    exists, ``stale`` when any structural disagreement or a source-hash mismatch
    is found, else ``consistent``.
    """
    with dictionary_lock(root, exclusive=False):
        return _check_dictionary_locked(root)


def _check_dictionary_locked(root: Path | str) -> dict[str, Any]:
    """check_dictionary body without the lock — for callers already holding the
    dictionary lock (e.g. ensure_dictionary_consistent under LOCK_EX)."""
    root_path = Path(root)
    corpus = derive_active_corpus(root_path)

    l0_path = root_path / _DICT_L0
    l1_dir = root_path / _DICT_L1_DIR
    l0_text = l0_path.read_text(encoding="utf-8") if l0_path.exists() else None
    l1_files: dict[str, str] = {}
    if l1_dir.exists():
        for p in sorted(l1_dir.glob("*.md")):
            l1_files[p.stem] = p.read_text(encoding="utf-8")

    state = load_state(root_path)
    return _evaluate(
        corpus, l0_text, l1_files, state.source_hash,
        state_status=state.status, state_rendered_hash=state.rendered_hash,
    )
