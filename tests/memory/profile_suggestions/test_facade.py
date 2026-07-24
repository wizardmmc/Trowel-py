from __future__ import annotations

import contextlib
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

import trowel_py.memory.profile_suggestions as profile_suggestions
from tests.memory.profile_suggestions.support import suggestion


_FUNCTION_SIGNATURES = {
    "_load_queue": "(root: 'Path') -> 'tuple[list[Suggestion], str]'",
    "_queue_path": "(root: 'Path') -> 'Path'",
    "_suggestion_from_dict": "(item: 'dict[str, object]') -> 'Suggestion'",
    "_suggestions_lock": "(root: 'Path')",
    "_write_queue": (
        "(root: 'Path', items: 'Sequence[Suggestion]', *, updated: 'str') -> 'None'"
    ),
    "append_suggestions": (
        "(root: 'Path', items: 'Sequence[Suggestion]', *, updated: 'str') -> 'None'"
    ),
    "load_suggestions": "(root: 'Path') -> 'list[Suggestion]'",
    "pending_suggestions": (
        "(root: 'Path', *, current_policy_version: 'int' = 2) -> 'list[Suggestion]'"
    ),
    "suggestion_to_dict": "(s: 'Suggestion') -> 'dict[str, object]'",
    "update_suggestion_status": (
        "(root: 'Path', suggestion_id: 'str', status: 'SuggestionStatus') -> 'None'"
    ),
}


def test_facade_functions_keep_signatures_and_module_identity() -> None:
    for name, signature in _FUNCTION_SIGNATURES.items():
        function = getattr(profile_suggestions, name)
        assert str(inspect.signature(function)) == signature
        assert function.__module__ == "trowel_py.memory.profile_suggestions"


def test_facade_keeps_old_direct_symbols() -> None:
    expected = {
        "PROFILE_DISTILL_POLICY_VERSION",
        "Path",
        "ProfileDimension",
        "Sequence",
        "Suggestion",
        "SuggestionStatus",
        "annotations",
        "cast",
        "contextlib",
        "fcntl",
        "json",
        "load_suggestions",
        "logger",
        "logging",
        "os",
        "pending_suggestions",
        "replace",
        "suggestion_to_dict",
        "update_suggestion_status",
    }
    assert expected <= set(vars(profile_suggestions))


def test_facade_codec_dependencies_are_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cast_calls: list[tuple[object, object]] = []

    def fake_cast(target: object, value: object) -> object:
        cast_calls.append((target, value))
        return value

    monkeypatch.setattr(
        profile_suggestions,
        "_VALID_DIMS",
        frozenset({"custom"}),
    )
    monkeypatch.setattr(
        profile_suggestions,
        "ProfileDimension",
        "PATCH_DIM",
    )
    monkeypatch.setattr(
        profile_suggestions,
        "SuggestionStatus",
        "PATCH_STATUS",
    )
    monkeypatch.setattr(profile_suggestions, "cast", fake_cast)
    loaded = profile_suggestions._suggestion_from_dict(
        {"id": "s1", "dimension": "custom"}
    )
    assert loaded.dimension == "custom"
    assert cast_calls == [
        ("PATCH_DIM", "custom"),
        ("PATCH_STATUS", "pending"),
    ]


def test_facade_serializer_patch_flows_to_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        profile_suggestions,
        "suggestion_to_dict",
        lambda item: {"patched": item.id},
    )
    profile_suggestions.append_suggestions(
        tmp_path,
        [suggestion(id_="s1")],
        updated="2026-07-15",
    )
    payload = (tmp_path / "meta" / "profile-suggestions.json").read_text(
        encoding="utf-8"
    )
    assert '"patched": "s1"' in payload


def test_append_read_write_remain_inside_facade_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def fake_lock(_root: Path):
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    def fake_load(_root: Path):
        events.append("load")
        return [], ""

    def fake_write(
        _root: Path,
        _items: object,
        *,
        updated: str,
    ) -> None:
        assert updated == "2026-07-15"
        events.append("write")

    monkeypatch.setattr(profile_suggestions, "_suggestions_lock", fake_lock)
    monkeypatch.setattr(profile_suggestions, "_load_queue", fake_load)
    monkeypatch.setattr(profile_suggestions, "_write_queue", fake_write)
    profile_suggestions.append_suggestions(
        tmp_path,
        [],
        updated="2026-07-15",
    )
    assert events == ["enter", "load", "write", "exit"]


def test_facade_lock_uses_exclusive_then_releases_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []

    class FakeFileLock:
        LOCK_EX = 2
        LOCK_UN = 8

        @staticmethod
        def flock(fd: int, operation: int) -> None:
            events.append(("flock", fd, operation))

    fake_os = SimpleNamespace(
        O_CREAT=64,
        O_RDWR=2,
        open=lambda path, flags: events.append(("open", path, flags)) or 7,
        close=lambda fd: events.append(("close", fd)),
    )
    monkeypatch.setattr(profile_suggestions, "fcntl", FakeFileLock)
    monkeypatch.setattr(profile_suggestions, "os", fake_os)
    with profile_suggestions._suggestions_lock(tmp_path):
        events.append(("body",))
    assert events == [
        ("open", str(tmp_path / "meta" / ".suggestions.lock"), 66),
        ("flock", 7, 2),
        ("body",),
        ("flock", 7, 8),
        ("close", 7),
    ]


def test_facade_lock_is_noop_without_fcntl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile_suggestions, "fcntl", None)
    with profile_suggestions._suggestions_lock(tmp_path):
        pass
    assert not (tmp_path / "meta").exists()
