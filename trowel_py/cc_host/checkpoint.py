"""Turn-level git checkpoints + revert for the CC host (slice-026 E1).

Borrows conductor's ``checkpointer.sh`` approach — private refs + parentless
``commit-tree``, HEAD never moves, working tree untouched by save — and
extends the commit message with the CC jsonl cut point so a revert can also
truncate the conversation log.

A checkpoint lives at ``refs/trowel-checkpoints/<turn_id>`` and captures the
full worktree (tracked + untracked, honoring .gitignore). ``revert`` restores
the worktree from that snapshot (HEAD intentionally NOT moved — reverting a
turn must not rewrite the user's branch history) and truncates the recorded
CC jsonl back to the byte offset captured at turn-start, so ``cc --resume``
continues as if the turn never happened.

Metadata persists in the commit message (no DB, no sidecar): a process
restart rebuilds the in-memory index via :func:`list_checkpoints`.
"""
from __future__ import annotations

import datetime as _dt
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REF_NAMESPACE = "refs/trowel-checkpoints"

_IDENTITY_NAME = "Checkpointer"
_IDENTITY_EMAIL = "checkpointer@noreply"


class NotAGitRepoError(RuntimeError):
    """workdir is not inside a git work tree — checkpoints unavailable."""


class UnknownCheckpointError(RuntimeError):
    """No checkpoint ref exists for the given turn_id."""


@dataclass(frozen=True)
class CheckpointMeta:
    """One saved checkpoint's identity + the CC jsonl cut point.

    Attributes:
        turn_id: the trowel turn this snapshot was taken before.
        jsonl_path: absolute path to the CC session jsonl, or None when the
            turn had no resumable jsonl yet (fresh session, turn 1).
        jsonl_offset: byte offset into the jsonl at turn-start; revert
            truncates the file back to here. None alongside jsonl_path=None.
        created_at: ISO-8601 UTC with microseconds; also drives gc ordering.
    """

    turn_id: str
    jsonl_path: str | None
    jsonl_offset: int | None
    created_at: str


# ── public API ─────────────────────────────────────────────────────────────


def is_git_repo(workdir: str | os.PathLike) -> bool:
    """True when *workdir* is inside a git work tree."""
    proc = subprocess.run(
        ["git", "-C", str(workdir), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
    )
    return proc.returncode == 0


def save(
    workdir: str | os.PathLike,
    turn_id: str,
    *,
    cc_session_jsonl_path: str | None = None,
    jsonl_offset: int | None = None,
) -> CheckpointMeta:
    """Snapshot the worktree as a private ref before a turn runs.

    Captures tracked + untracked files (honoring .gitignore) into a parentless
    commit under ``refs/trowel-checkpoints/<turn_id>``. HEAD, the index, and
    the working tree are NOT modified — ``git status`` looks identical before
    and after.

    Args:
        workdir: the CC workdir (need not be the repo root; git resolves the
            toplevel automatically and snapshots the whole repo).
        turn_id: the trowel turn id this checkpoint belongs to.
        cc_session_jsonl_path: absolute path to the CC session jsonl, recorded
            so revert can truncate it. None for a fresh session's turn 1.
        jsonl_offset: byte offset into the jsonl captured now; revert
            truncates back to here.

    Returns:
        the saved :class:`CheckpointMeta`.

    Raises:
        NotAGitRepoError: workdir is not inside a git work tree.
    """
    root = _require_repo(workdir)
    created = _now_iso()
    index_tree = _git(root, "write-tree").strip()
    worktree_tree = _snapshot_worktree_tree(root, index_tree)
    meta = CheckpointMeta(
        turn_id=turn_id,
        jsonl_path=cc_session_jsonl_path,
        jsonl_offset=jsonl_offset,
        created_at=created,
    )
    commit_oid = _commit_tree(root, worktree_tree, _encode_message(meta), created)
    _git(root, "update-ref", f"{_REF_NAMESPACE}/{turn_id}", commit_oid)
    return meta


def revert(workdir: str | os.PathLike, turn_id: str) -> CheckpointMeta:
    """Restore the worktree + truncate the jsonl to undo a turn.

    Restores files (tracked + removes untracked created after the snapshot)
    from the checkpoint tree. HEAD is intentionally NOT moved — reverting a
    turn must not rewrite branch history. Then truncates the recorded CC jsonl
    back to ``jsonl_offset`` so ``cc --resume`` reloads the shorter history.

    Args:
        workdir: the CC workdir.
        turn_id: the turn to undo (also drops every later turn implicitly).

    Returns:
        the reverted checkpoint's :class:`CheckpointMeta`.

    Raises:
        NotAGitRepoError: workdir is not inside a git work tree.
        UnknownCheckpointError: no checkpoint ref for turn_id.
    """
    root = _require_repo(workdir)
    ref = f"{_REF_NAMESPACE}/{turn_id}"
    verify = subprocess.run(
        ["git", "-C", root, "rev-parse", "--verify", "--quiet", ref],
        capture_output=True,
    )
    if verify.returncode != 0:
        raise UnknownCheckpointError(turn_id)
    commit_oid = verify.stdout.decode().strip()
    meta = _meta_for_commit(root, turn_id, commit_oid)
    tree = _git(root, "rev-parse", f"{commit_oid}^{{tree}}").strip()
    # Restore index + worktree to the snapshot; HEAD stays put.
    _git(root, "read-tree", "--reset", "-u", tree)
    # Drop untracked files/dirs created after the snapshot (gitignored kept).
    _git(root, "clean", "-fd")
    if meta.jsonl_path and meta.jsonl_offset is not None:
        _truncate_file(meta.jsonl_path, meta.jsonl_offset)
    return meta


def list_checkpoints(workdir: str | os.PathLike) -> list[CheckpointMeta]:
    """Return all checkpoints for the repo, newest-first by created_at.

    Used at startup to rebuild the in-memory ``(turn_id → offset)`` index from
    git refs alone (no DB). Empty list when workdir is not a git repo or has
    no checkpoints.
    """
    if not is_git_repo(workdir):
        return []
    root = _toplevel(workdir)
    raw = _git(
        root,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
        _REF_NAMESPACE,
    )
    metas: list[CheckpointMeta] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            continue
        refname, oid = parts
        turn_id = refname.rsplit("/", 1)[-1]
        metas.append(_meta_for_commit(root, turn_id, oid))
    metas.sort(key=lambda m: (m.created_at, m.turn_id), reverse=True)
    return metas


def gc(workdir: str | os.PathLike, *, keep: int = 50) -> int:
    """Keep only the *keep* newest checkpoints, delete the rest.

    Sorts by committer date desc (cheap: one ``for-each-ref``, no per-ref
    message reads) so this is safe to call on every save.

    Args:
        workdir: the CC workdir.
        keep: how many of the newest checkpoints to retain.

    Returns:
        number of checkpoint refs deleted.
    """
    if not is_git_repo(workdir):
        return 0
    root = _toplevel(workdir)
    raw = _git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "--sort=-committerdate",
        _REF_NAMESPACE,
    )
    refs = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(refs) <= keep:
        return 0
    for ref in refs[keep:]:
        _git(root, "update-ref", "-d", ref)
    return len(refs) - keep


# ── git plumbing ───────────────────────────────────────────────────────────


def _require_repo(workdir: str | os.PathLike) -> str:
    """Return the repo toplevel, or raise NotAGitRepoError."""
    if not is_git_repo(workdir):
        raise NotAGitRepoError(f"{workdir} is not a git work tree")
    return _toplevel(workdir)


def _toplevel(workdir: str | os.PathLike) -> str:
    return _git(workdir, "rev-parse", "--show-toplevel").strip()


def _git(cwd: str | os.PathLike, *args: str, env: dict | None = None) -> str:
    """Run git -C cwd args; raise RuntimeError with stderr on failure."""
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def _snapshot_worktree_tree(root: str, seed_tree: str) -> str:
    """Write a tree of the full worktree (tracked + untracked) via a temp index.

    Seeds the temp index from the current index tree first so tracked files
    that match .gitignore are preserved (gitignore only filters untracked
    paths). Ported from conductor's save.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="trowel-cp-"))
    try:
        tmp_index = tmp_dir / "index"  # must NOT pre-exist (git requirement)
        env = {**os.environ, "GIT_INDEX_FILE": str(tmp_index)}
        if seed_tree:
            _git(root, "read-tree", seed_tree, env=env)
        _git(root, "add", "-A", "--", ".", env=env)
        return _git(root, "write-tree", env=env).strip()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _commit_tree(root: str, tree: str, message: str, iso: str) -> str:
    """commit-tree with a neutral Checkpointer identity (no parent, no HEAD)."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": _IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": _IDENTITY_EMAIL,
        "GIT_AUTHOR_DATE": iso,
        "GIT_COMMITTER_NAME": _IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": _IDENTITY_EMAIL,
        "GIT_COMMITTER_DATE": iso,
    }
    proc = subprocess.run(
        ["git", "-C", root, "commit-tree", tree],
        input=message,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"commit-tree failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _meta_for_commit(root: str, turn_id: str, commit_oid: str) -> CheckpointMeta:
    """Parse a checkpoint's commit message back into CheckpointMeta."""
    body = _git(root, "log", "-1", "--format=%B", commit_oid)
    return _decode_message(body, turn_id)


def _truncate_file(path: str, offset: int) -> None:
    """Truncate *path* to *offset* bytes (revert's jsonl cut).

    The offset was captured at turn-start, when the jsonl ended at a complete
    entry (``\\n``), so the cut normally lands on a line boundary. Defensively,
    if a partial-line race left a non-``\\n`` at ``offset-1``, scan backward to
    the last ``\\n`` so we never hand cc a half-line on ``--resume``.
    """
    p = Path(path)
    if not p.is_file():
        return
    with p.open("r+b") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        if offset >= size:
            return  # nothing to truncate
        cut = offset
        if offset > 0:
            fh.seek(offset - 1)
            if fh.read(1) != b"\n":
                fh.seek(0)
                head_bytes = fh.read(offset)
                last_nl = head_bytes.rfind(b"\n")
                cut = last_nl + 1 if last_nl >= 0 else 0
        fh.truncate(cut)


def _now_iso() -> str:
    """Current UTC moment as ISO-8601 with microsecond precision."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="microseconds")


# ── message encode/decode ──────────────────────────────────────────────────


def _encode_message(meta: CheckpointMeta) -> str:
    """Serialize meta to the commit message body (conductor-style kv lines)."""
    jsonl_path = meta.jsonl_path or ""
    jsonl_offset = "" if meta.jsonl_offset is None else str(meta.jsonl_offset)
    return (
        f"checkpoint:{meta.turn_id}\n"
        f"jsonl-path {jsonl_path}\n"
        f"jsonl-offset {jsonl_offset}\n"
        f"created {meta.created_at}\n"
    )


def _decode_message(body: str, turn_id: str) -> CheckpointMeta:
    """Parse a commit message body back into CheckpointMeta.

    Tolerant: missing fields default to None / empty. Unknown lines ignored.
    """
    jsonl_path: str | None = None
    jsonl_offset: int | None = None
    created = ""
    for line in body.splitlines():
        if line.startswith("jsonl-path "):
            val = line[len("jsonl-path ") :].strip()
            jsonl_path = val or None
        elif line.startswith("jsonl-offset "):
            val = line[len("jsonl-offset ") : ].strip()
            if val:
                try:
                    jsonl_offset = int(val)
                except ValueError:
                    # malformed offset (corrupt/edit commit message) — don't
                    # crash list_checkpoints at startup; treat as unknown.
                    jsonl_offset = None
        elif line.startswith("created "):
            created = line[len("created ") :].strip()
    return CheckpointMeta(
        turn_id=turn_id,
        jsonl_path=jsonl_path,
        jsonl_offset=jsonl_offset,
        created_at=created,
    )
