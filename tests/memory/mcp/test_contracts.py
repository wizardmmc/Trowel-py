"""URI、候选读取与宿主身份契约。"""

from __future__ import annotations

import pytest

from tests.memory.mcp.support import note
from trowel_py.memory.mcp_server import (
    _SEARCH_DESC,
    _identity_from_env,
    parse_memory_uri,
    requires_read,
)


def test_parse_uri_valid() -> None:
    assert parse_memory_uri("memory://notes/my-note") == "my-note"


@pytest.mark.parametrize(
    "bad",
    [
        "http://x/y",
        "memory://notes/",
        "memory://notes/../etc",
        "memory://notes/a/b",
        "memory://notes/.hidden",
    ],
)
def test_parse_uri_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_memory_uri(bad)


def test_requires_read_by_kind() -> None:
    assert requires_read(note(kind="gotcha"))
    assert requires_read(note(kind="procedure"))
    assert requires_read(note(kind="hypothesis"))
    assert not requires_read(note(kind="fact"))


def test_requires_read_by_verification() -> None:
    assert requires_read(note(kind="fact", verification="inferred-untested"))
    assert not requires_read(note(kind="fact", verification="verified"))


def test_search_description_requires_read_hint() -> None:
    assert "requires_read" in _SEARCH_DESC
    assert "memory.read" in _SEARCH_DESC


def test_identity_from_env_reads_host_neutral_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TROWEL_SESSION_ID", "trowel-codex-1")
    monkeypatch.setenv("TROWEL_HOST_KIND", "codex")
    monkeypatch.setenv("TROWEL_NATIVE_SESSION_ID", "codex-thread-abc")
    monkeypatch.delenv("CC_SESSION_ID", raising=False)

    identity = _identity_from_env()

    assert identity["host_kind"] == "codex"
    assert identity["native_session_id"] == "codex-thread-abc"
    assert identity["cc_session_id"] == ""
    assert identity["trowel_session_id"] == "trowel-codex-1"


def test_identity_from_env_backfills_from_legacy_cc_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TROWEL_SESSION_ID", "t-1")
    monkeypatch.setenv("CC_SESSION_ID", "cc-sid-legacy")
    monkeypatch.delenv("TROWEL_HOST_KIND", raising=False)
    monkeypatch.delenv("TROWEL_NATIVE_SESSION_ID", raising=False)

    identity = _identity_from_env()

    assert identity["host_kind"] == "cc"
    assert identity["native_session_id"] == "cc-sid-legacy"
    assert identity["cc_session_id"] == "cc-sid-legacy"


def test_identity_from_env_cc_path_stamps_both_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TROWEL_SESSION_ID", "t-1")
    monkeypatch.setenv("TROWEL_HOST_KIND", "cc")
    monkeypatch.setenv("TROWEL_NATIVE_SESSION_ID", "cc-sid-1")
    monkeypatch.setenv("CC_SESSION_ID", "cc-sid-1")

    identity = _identity_from_env()

    assert identity["host_kind"] == "cc"
    assert identity["native_session_id"] == "cc-sid-1"
    assert identity["cc_session_id"] == "cc-sid-1"
