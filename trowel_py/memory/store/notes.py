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
    """为 ``MemoryStore`` 提供 Note 的读取、写入和字段更新能力。

    组合后的仓储必须提供 ``root``。``record_ref()`` 与
    ``update_note_fields()`` 使用同一种 POSIX advisory lock；普通读取和
    ``write_note()`` 不参与该锁，文件重写也不是原子发布。I/O、UTF-8、YAML
    序列化及字段转换错误直接传播。
    """

    root: Path

    def load_notes(self, filter: dict[str, Any] | None = None) -> list[Note]:
        """读取 ``notes/*.md`` 中可转换的 Note，并按可选条件筛选。

        文件按路径排序。缺失或无法解析为映射的 frontmatter，以及 ``type`` 不
        是 ``note`` 的文件会记录警告并跳过；其他字段不执行 schema 校验，转换
        失败时异常传播。非空 filter 支持 ``status``、``retired`` 和 ``tag``，
        未知键被忽略。
        """

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
        """按文件 stem 读取一条 Note。

        文件不存在、frontmatter 无法解析为映射或 ``type`` 不是 ``note`` 时
        返回 None，不记录警告。``note_id`` 未清理路径字符；其他读取和字段转换
        错误直接传播。
        """

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
        """读取可转换的 Note，并返回其文件 stem。

        跳过规则、筛选条件和路径顺序与 ``load_notes()`` 相同，但跳过文件时不
        记录警告。
        """

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
        """局部校验 Note 字段，并写入当前未占用的文件 stem。

        stem 由标题生成；若已存在则从 ``-2`` 开始递增。选择与写入之间没有锁，
        并发调用不能保证各自获得不同 stem。名称以 ``__`` 开头的字段不进入
        frontmatter，``__body`` 用作正文，其他未知字段保留。显式 ``type`` 不
        会被强制或校验，调用方必须传入 ``note``。

        Args:
            entry: 待写入的 Note 字段和可选 ``__body``。

        Returns:
            实际使用的文件 stem。

        Raises:
            ValueError: entry 未通过 Note 的局部 schema 校验。
        """

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
        """在独占文件锁内递增 ``refs`` 并覆盖 ``last_ref``。

        frontmatter 和正文会整体重写，未修改字段保留；不校验顶层 ``type``。
        ``refs`` 的假值按 0 处理，其他值通过 ``int()`` 转换。``note_id`` 未清理
        路径字符，本方法只与使用同类 advisory lock 的写入协作。

        Raises:
            FileNotFoundError: 对应文件不存在。
            ValueError: 文件没有可解析为映射的 frontmatter。
        """

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
        """返回首个同时匹配来源会话和内容哈希的文件 stem。

        文件按路径排序；缺失、空或无法解析为映射的 frontmatter 会被跳过，
        但不校验顶层 ``type``。``source_sessions`` 的假值按空列表处理，其他
        值直接使用 ``in`` 判断：序列按成员匹配，字符串按子串匹配，不支持成员
        判断的真值会传播 ``TypeError``；``content_hash`` 使用相等比较。没有
        匹配项时返回 None。
        """

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
        """在独占文件锁内合并 frontmatter 字段并保留正文。

        ``fields`` 直接覆盖同名键，不执行 Note schema 校验，也不特殊处理
        ``__`` 前缀。``note_id`` 未清理路径字符，本方法只与使用同类 advisory
        lock 的写入协作。

        Raises:
            FileNotFoundError: 对应文件不存在。
            ValueError: 文件没有可解析为映射的 frontmatter。
        """

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
        """返回当前未占用的标题 slug，不创建或锁定对应文件。

        基础 slug 已存在时依次尝试 ``-2``、``-3`` 等后缀。
        """
        base = _slugify(title)
        slug, i = base, 2
        while (self.root / _NOTES_DIR / f"{slug}.md").exists():
            slug, i = f"{base}-{i}", i + 1
        return slug
