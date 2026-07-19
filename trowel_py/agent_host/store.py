"""JSON-backed persistence for :class:`SessionBinding` (slice-072).

A single JSON file at ``~/.trowel/agent_sessions.json`` (overridable via the
``TROWEL_AGENT_SESSIONS_PATH`` env var — mirrors the memory root pattern so
tests pin it to tmp) holds every binding.

SQLite is deliberately avoided here: the dataset is tiny (one row per
session), writes are rare, and SQLite's ``check_same_thread`` interactions
with FastAPI TestClient's anyio portal are a known trap in this codebase
(see the verified note on asyncio.to_thread + sqlite). A single JSON file
keeps the failure surface flat and makes test isolation trivial.

Writes are atomic (tmp file + ``os.replace``) so a crash mid-write never
leaves a half file (spec: binding migration must be safe; "删除数据库是重点事故"
— a corrupt bindings file must never lose prior bindings).

The store is synchronous. Binding CRUD is fast, bounded I/O on a small file,
so FastAPI route handlers call it directly without a thread offload.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.agent_host.binding import SessionBinding, binding_from_dict

_SCHEMA_VERSION = 1
_DEFAULT_PATH = Path.home() / ".trowel" / "agent_sessions.json"


def resolve_bindings_path() -> Path:
    """Return the bindings file path, honoring ``TROWEL_AGENT_SESSIONS_PATH``.

    The env var is the test/relay override (set it to a tmp path in tests).
    Production leaves it unset and gets the default ``~/.trowel`` location.
    """

    override = os.environ.get("TROWEL_AGENT_SESSIONS_PATH")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_PATH


class BindingStore:
    """Tiny JSON-backed CRUD store for :class:`SessionBinding`.

    Each method reloads from disk so two store instances on the same file
    (e.g. a restart) never serve stale state. The dataset is small enough
    that the re-read cost is negligible and the simplicity is worth it.
    """

    def __init__(self, path: Path) -> None:
        """Store bindings at ``path`` (created lazily on first write).

        Args:
            path: the JSON file to back the store with. Parent dirs are
                created on write, so ``path`` may point into a not-yet-existing
                directory.
        """

        self._path = path

    @property
    def path(self) -> Path:
        """The backing JSON file path."""

        return self._path

    # --------------------------------------------------------------- internals

    def _load_raw(self) -> dict[str, dict[str, Any]]:
        """Read every binding as its raw persisted dict, keyed by session id.

        Returns an empty dict when the file is absent or corrupt — a corrupt
        file should not crash the hub (bindings are best-effort metadata; the
        live ``_REGISTRY`` / Codex manager remain authoritative for running
        sessions).
        """

        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            return {}
        return {
            sid: payload
            for sid, payload in sessions.items()
            if isinstance(payload, dict)
        }

    def _save_raw(self, sessions: dict[str, dict[str, Any]]) -> None:
        """Atomically write the full payload (tmp + ``os.replace``).

        Parent dirs are created here so a not-yet-existing path works on the
        first write.
        """

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _SCHEMA_VERSION, "sessions": sessions}
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except BaseException:
            # Best-effort cleanup of the tmp fragment on any failure; the
            # os.replace either fully happened or did not, so the live file
            # is never half-written.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # --------------------------------------------------------------- public API

    def put(self, binding: SessionBinding) -> None:
        """Insert or replace (upsert) a binding by its session id."""

        sessions = self._load_raw()
        sessions[binding.session_id] = binding.to_dict()
        self._save_raw(sessions)

    def get(self, session_id: str) -> SessionBinding | None:
        """Return the binding for ``session_id``, or ``None`` when absent."""

        raw = self._load_raw().get(session_id)
        return binding_from_dict(raw) if raw is not None else None

    def list_all(self) -> list[SessionBinding]:
        """Return every persisted binding (order is file insertion order)."""

        return [binding_from_dict(payload) for payload in self._load_raw().values()]

    def delete(self, session_id: str) -> bool:
        """Delete a binding. Returns ``True`` if it existed, else ``False``."""

        sessions = self._load_raw()
        if session_id not in sessions:
            return False
        del sessions[session_id]
        self._save_raw(sessions)
        return True

    def update_native(
        self,
        session_id: str,
        *,
        native_session_id: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        permission: str | None = None,
        connected: bool | None = None,
        running: bool | None = None,
        effective_permission_profile: str | None = None,
        effective_sandbox: str | None = None,
        effective_approval: str | None = None,
        network_access: bool | None = None,
    ) -> SessionBinding:
        """Write back native facts immutably and return the new binding.

        Only keyword args explicitly passed (non-``None``) are overridden;
        ``runtime`` is NEVER changeable here (spec C-1 frozen). The update is
        a read-modify-write of the whole file under a single atomic save, so
        concurrent calls serialise on the filesystem (acceptable — updates are
        rare and per-session).

        Raises:
            KeyError: if ``session_id`` is not in the store.
        """

        existing = self.get(session_id)
        if existing is None:
            raise KeyError(session_id)
        changes: dict[str, Any] = {
            "updated_at": datetime.now().isoformat(timespec="seconds")
        }
        if native_session_id is not None:
            changes["native_session_id"] = native_session_id
        if model is not None:
            changes["model"] = model
        if effort is not None:
            changes["effort"] = effort
        if permission is not None:
            changes["permission"] = permission
        if connected is not None:
            changes["connected"] = connected
        if running is not None:
            changes["running"] = running
        if effective_permission_profile is not None:
            changes["effective_permission_profile"] = effective_permission_profile
        if effective_sandbox is not None:
            changes["effective_sandbox"] = effective_sandbox
        if effective_approval is not None:
            changes["effective_approval"] = effective_approval
        if network_access is not None:
            changes["network_access"] = network_access
        updated = replace(existing, **changes)
        self.put(updated)
        return updated
