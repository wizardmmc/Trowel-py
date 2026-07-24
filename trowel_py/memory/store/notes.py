"""Knowledge notes 的读取、写入与并发更新。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from trowel_py.memory.schema import validate_entry
from trowel_py.memory.types import Note, NoteId

from .codec import (
    _dump_frontmatter,
    _matches,
    _note_from_fm,
    _ordered_note_frontmatter,
    _slugify,
    _split_frontmatter,
)

logger = logging.getLogger("trowel_py.memory.store")
_NOTES_DIR = "notes"


class _NotesStore:
    root: Path

    def load_notes(self, filter: dict[str, Any] | None = None) -> list[Note]:

        notes_dir = self.root / _NOTES_DIR
        if not notes_dir.exists():
            return []
        notes: list[Note] = []
        for p in sorted(notes_dir.glob("*.md")):
            fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
            if fm is None:
                logger.warning("note %s has no/invalid frontmatter, skipped", p.name)
                continue
            note = _note_from_fm(fm, body)
            if note is None:
                logger.warning(
                    "note %s frontmatter type is not 'note', skipped", p.name
                )
                continue
            notes.append(note)
        if filter:
            notes = [n for n in notes if _matches(n, filter)]
        return notes

    def load_note(self, note_id: NoteId) -> Note | None:

        path = self.root / _NOTES_DIR / f"{note_id}.md"
        if not path.exists():
            return None
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if fm is None:
            return None
        return _note_from_fm(fm, body)

    def load_notes_with_id(
        self, filter: dict[str, Any] | None = None
    ) -> list[tuple[NoteId, Note]]:

        notes_dir = self.root / _NOTES_DIR
        if not notes_dir.exists():
            return []
        out: list[tuple[NoteId, Note]] = []
        for p in sorted(notes_dir.glob("*.md")):
            fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
            if fm is None:
                continue
            note = _note_from_fm(fm, body)
            if note is None:
                continue
            out.append((p.stem, note))
        if filter:
            out = [(i, n) for i, n in out if _matches(n, filter)]
        return out

    def write_note(self, entry: dict[str, Any]) -> NoteId:

        result = validate_entry("note", entry)
        if not result.ok:
            raise ValueError(f"invalid note: {result.errors}")
        slug = self._unique_slug(str(entry.get("title", "")).strip())
        fm = _ordered_note_frontmatter(entry)
        path = self.root / _NOTES_DIR / f"{slug}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _dump_frontmatter(fm, entry.get("__body", "")), encoding="utf-8"
        )
        return slug

    def record_ref(self, note_id: NoteId, date: str) -> None:
        """用文件锁保护 refs 的 read-modify-write。"""

        import fcntl

        path = self.root / _NOTES_DIR / f"{note_id}.md"
        if not path.exists():
            raise FileNotFoundError(note_id)
        with path.open("r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                fm, body = _split_frontmatter(f.read())
                if fm is None:
                    raise ValueError(f"note {note_id!r} has no frontmatter")
                fm["refs"] = int(fm.get("refs") or 0) + 1
                fm["last_ref"] = date
                f.seek(0)
                f.truncate()
                f.write(_dump_frontmatter(fm, body))
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def find_note_by_source(
        self, cc_session_id: str, content_hash: str
    ) -> NoteId | None:

        notes_dir = self.root / _NOTES_DIR
        if not notes_dir.exists():
            return None
        for p in sorted(notes_dir.glob("*.md")):
            fm, _body = _split_frontmatter(p.read_text(encoding="utf-8"))
            if not fm:
                continue
            sources = fm.get("source_sessions") or []
            if cc_session_id in sources and fm.get("content_hash") == content_hash:
                return p.stem
        return None

    def update_note_fields(self, note_id: NoteId, fields: dict[str, Any]) -> None:
        """与 record_ref 使用同一文件锁，避免并发更新丢失。"""

        path = self.root / _NOTES_DIR / f"{note_id}.md"
        if not path.exists():
            raise FileNotFoundError(note_id)

        import fcntl

        with path.open("r+", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                fm, body = _split_frontmatter(f.read())
                if fm is None:
                    raise ValueError(f"note {note_id!r} has no frontmatter")
                fm.update(fields)
                f.seek(0)
                f.truncate()
                f.write(_dump_frontmatter(fm, body))
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def _unique_slug(self, title: str) -> str:
        base = _slugify(title)
        slug, i = base, 2
        while (self.root / _NOTES_DIR / f"{slug}.md").exists():
            slug, i = f"{base}-{i}", i + 1
        return slug
