from __future__ import annotations

import contextlib
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import trowel_py.memory.dictionary_check as dictionary_check
from trowel_py.memory.types import Note


_FUNCTION_SIGNATURES = {
    "_check_dictionary_locked": ("(root: 'Path | str') -> 'dict[str, Any]'"),
    "_evaluate": (
        "(corpus: 'list[tuple[str, Note]]', l0_text: 'str | None', "
        "l1_files: 'dict[str, str]', state_hash: 'str | None', *, "
        "state_status: 'str' = 'consistent', "
        "state_rendered_hash: 'str | None' = None, "
        "baseline_required: 'bool' = True) -> 'dict[str, Any]'"
    ),
    "_parse_l0": "(l0_text: 'str') -> 'list[tuple[str, int]]'",
    "_parse_l1_stems": "(l1_text: 'str') -> 'list[str]'",
    "check_dictionary": "(root: 'Path | str') -> 'dict[str, Any]'",
    "compute_rendered_hash": ("(l0_text: 'str', l1_files: 'dict[str, str]') -> 'str'"),
    "compute_source_hash": ("(corpus: 'list[tuple[str, Note]]') -> 'str'"),
    "derive_active_corpus": ("(root: 'Path | str') -> 'list[tuple[str, Note]]'"),
}


def test_facade_functions_keep_signatures_and_module_identity() -> None:
    for name, signature in _FUNCTION_SIGNATURES.items():
        function = getattr(dictionary_check, name)
        assert str(inspect.signature(function)) == signature
        assert function.__module__ == "trowel_py.memory.dictionary_check"


def test_facade_keeps_old_direct_symbols() -> None:
    expected = {
        "Any",
        "MemoryStore",
        "Note",
        "Path",
        "annotations",
        "check_dictionary",
        "compute_rendered_hash",
        "compute_source_hash",
        "derive_active_corpus",
        "dictionary_lock",
        "hashlib",
        "load_state",
        "re",
    }
    assert expected <= set(vars(dictionary_check))


def test_facade_hash_and_regex_dependencies_are_dynamic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeHash:
        @staticmethod
        def hexdigest() -> str:
            return "0123456789abcdef-rest"

    seen: list[bytes] = []
    fake_hashlib = SimpleNamespace(
        sha256=lambda payload: seen.append(payload) or FakeHash()
    )
    monkeypatch.setattr(dictionary_check, "hashlib", fake_hashlib)
    monkeypatch.setattr(
        dictionary_check,
        "_L0_DOMAIN_RE",
        re.compile(r"^X:(\S+):(\d+)$"),
    )
    monkeypatch.setattr(
        dictionary_check,
        "_L1_STEM_ANCHOR_RE",
        re.compile(r"A:(\S+)"),
    )
    monkeypatch.setattr(
        dictionary_check,
        "_L1_STEM_RE",
        re.compile(r"L:(\S+)"),
    )

    assert dictionary_check.compute_source_hash([]) == "0123456789abcdef"
    assert dictionary_check.compute_rendered_hash("L0", {}) == ("0123456789abcdef")
    assert seen == [b"", b"L0"]
    assert dictionary_check._parse_l0("X:domain:3") == [("domain", 3)]
    assert dictionary_check._parse_l1_stems("L:legacy A:anchor") == ["anchor"]

    monkeypatch.setattr(
        dictionary_check,
        "_L1_STEM_ANCHOR_RE",
        re.compile(r"never:(\S+)"),
    )
    assert dictionary_check._parse_l1_stems("L:legacy") == ["legacy"]


def test_active_corpus_uses_current_facade_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    note = Note(type="note", title="A")

    class FakeStore:
        def __init__(self, root: object) -> None:
            calls.append(("init", root))

        def load_notes_with_id(
            self,
            filters: dict[str, str],
        ) -> list[tuple[str, Note]]:
            calls.append(("load", filters))
            return [("a", note)]

    monkeypatch.setattr(dictionary_check, "MemoryStore", FakeStore)
    assert dictionary_check.derive_active_corpus("root") == [("a", note)]
    assert calls == [
        ("init", "root"),
        ("load", {"status": "active"}),
    ]


def test_evaluator_uses_current_facade_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(
        dictionary_check,
        "compute_source_hash",
        lambda _corpus: events.append("source") or "source",
    )
    monkeypatch.setattr(
        dictionary_check,
        "_parse_l0",
        lambda _text: events.append("l0") or [],
    )
    monkeypatch.setattr(
        dictionary_check,
        "_parse_l1_stems",
        lambda _text: events.append("l1") or [],
    )
    monkeypatch.setattr(
        dictionary_check,
        "compute_rendered_hash",
        lambda _l0, _l1: events.append("rendered") or "rendered",
    )

    report = dictionary_check._evaluate(
        [],
        "L0",
        {"domain": "L1"},
        "source",
        state_rendered_hash="rendered",
    )
    assert report["status"] == "stale"
    assert events == ["source", "l0", "l1", "rendered"]


def test_public_check_wraps_locked_body_in_shared_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []

    @contextlib.contextmanager
    def fake_lock(root: object, *, exclusive: bool):
        events.append(("enter", root, exclusive))
        try:
            yield
        finally:
            events.append("exit")

    def fake_locked(root: object) -> dict[str, object]:
        events.append(("check", root))
        return {"status": "sentinel"}

    monkeypatch.setattr(dictionary_check, "dictionary_lock", fake_lock)
    monkeypatch.setattr(
        dictionary_check,
        "_check_dictionary_locked",
        fake_locked,
    )
    assert dictionary_check.check_dictionary("root") == {"status": "sentinel"}
    assert events == [
        ("enter", "root", False),
        ("check", "root"),
        "exit",
    ]


def test_locked_check_uses_current_facade_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = Note(type="note", title="A")
    corpus = [("a", note)]
    state = SimpleNamespace(
        source_hash="source",
        status="consistent",
        rendered_hash="rendered",
    )
    captured: dict[str, object] = {}

    def fake_corpus(root: Path) -> list[tuple[str, Note]]:
        captured["corpus_root"] = root
        return corpus

    monkeypatch.setattr(dictionary_check, "derive_active_corpus", fake_corpus)

    def fake_read(
        root: Path,
        *,
        l0_filename: str,
        l1_dirname: str,
    ) -> tuple[str, dict[str, str]]:
        captured["files"] = (root, l0_filename, l1_dirname)
        return "L0", {"domain": "L1"}

    monkeypatch.setattr(
        dictionary_check,
        "_read_dictionary_files",
        fake_read,
    )

    def fake_state(root: Path) -> object:
        captured["state_root"] = root
        return state

    monkeypatch.setattr(dictionary_check, "load_state", fake_state)

    def fake_evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        captured["evaluation"] = (args, kwargs)
        return {"status": "sentinel"}

    monkeypatch.setattr(dictionary_check, "_evaluate", fake_evaluate)
    assert dictionary_check._check_dictionary_locked(tmp_path) == {"status": "sentinel"}
    assert captured["corpus_root"] == tmp_path
    assert captured["files"] == (
        tmp_path,
        "dictionary-L0.md",
        "dictionary-L1",
    )
    assert captured["state_root"] == tmp_path
    assert captured["evaluation"] == (
        (corpus, "L0", {"domain": "L1"}, "source"),
        {
            "state_status": "consistent",
            "state_rendered_hash": "rendered",
        },
    )
