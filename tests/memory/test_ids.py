from __future__ import annotations

import uuid

from trowel_py.memory.ids import uuid7


def test_uuid7_is_uuid_version_7() -> None:
    u = uuid7(now_ms=1_700_000_000_000)
    assert isinstance(u, uuid.UUID)
    assert u.version == 7


def test_uuid7_str_is_36_chars() -> None:
    u = uuid7(now_ms=1_700_000_000_000)
    s = str(u)
    assert len(s) == 36
    assert s[14] == "7"


def test_two_uuid7_at_same_ms_differ() -> None:
    a = uuid7(now_ms=1_700_000_000_000)
    b = uuid7(now_ms=1_700_000_000_000)
    assert a != b


def test_uuid7_time_ordered() -> None:
    earlier = uuid7(now_ms=1_000)
    later = uuid7(now_ms=2_000)
    assert str(earlier) < str(later)


def test_uuid7_default_uses_wall_clock() -> None:
    u = uuid7()
    assert u.version == 7
    assert len(str(u)) == 36
