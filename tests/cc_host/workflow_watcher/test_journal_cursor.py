import os
from pathlib import Path

from trowel_py.cc_host.workflow_journal import JsonlCursor


def test_cursor_waits_for_partial_line_completion(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'{"sequence":')
    cursor = JsonlCursor()

    assert cursor.read(journal) == []

    with journal.open("ab") as stream:
        stream.write(b"1}\n")

    assert cursor.read(journal) == [b'{"sequence":1}']
    assert cursor.read(journal) == []


def test_cursor_waits_for_split_utf8_character(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    line = '{"label":"示例"}\n'.encode()
    split_at = line.index("示".encode()) + 1
    journal.write_bytes(line[:split_at])
    cursor = JsonlCursor()

    assert cursor.read(journal) == []

    with journal.open("ab") as stream:
        stream.write(line[split_at:])

    assert cursor.read(journal) == [line.rstrip(b"\n")]
    assert cursor.read(journal) == []


def test_cursor_returns_each_appended_line_once(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'{"sequence":1}\n')
    cursor = JsonlCursor()

    assert cursor.read(journal) == [b'{"sequence":1}']

    with journal.open("ab") as stream:
        stream.write(b'{"sequence":2}\n')

    assert cursor.read(journal) == [b'{"sequence":2}']
    assert cursor.read(journal) == []


def test_cursor_uses_only_lf_as_line_boundary(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'{"sequence":\r1}\r\n')

    assert JsonlCursor().read(journal) == [b'{"sequence":\r1}\r']


def test_cursor_restarts_after_same_inode_truncate(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'{"sequence":100,"padding":"long"}\n')
    cursor = JsonlCursor()
    original_identity = _identity(journal)

    assert cursor.read(journal) == [b'{"sequence":100,"padding":"long"}']

    with journal.open("wb") as stream:
        stream.write(b'{"sequence":2}\n')

    assert _identity(journal) == original_identity
    assert cursor.read(journal) == [b'{"sequence":2}']
    assert cursor.read(journal) == []


def test_cursor_restarts_after_inode_replacement(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    replacement = tmp_path / "replacement.jsonl"
    journal.write_bytes(b'{"sequence":1}\n')
    cursor = JsonlCursor()
    original_identity = _identity(journal)

    assert cursor.read(journal) == [b'{"sequence":1}']

    replacement.write_bytes(b'{"sequence":200,"padding":"longer replacement"}\n')
    os.replace(replacement, journal)

    assert _identity(journal) != original_identity
    assert cursor.read(journal) == [b'{"sequence":200,"padding":"longer replacement"}']
    assert cursor.read(journal) == []


def _identity(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino
