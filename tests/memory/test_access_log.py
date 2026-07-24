from __future__ import annotations

from pathlib import Path

from trowel_py.memory.access_log import (
    AccessRecord,
    OutcomeRecord,
    log_access,
    log_outcome,
    read_access_log,
    read_outcome_log,
)


def test_log_access_roundtrip(tmp_path: Path) -> None:
    rec = AccessRecord(
        ts="2026-07-11T10:00:00",
        trowel_session_id="trowel-1",
        cc_session_id="cc-1",
        toolUseId="call_abc",
        action="search",
        search_id="search-1",
        query="how to handle X",
        memory_id="",
    )
    log_access(tmp_path, rec)
    out = read_access_log(tmp_path)
    assert len(out) == 1
    assert out[0].action == "search"
    assert out[0].toolUseId == "call_abc"
    assert out[0].search_id == "search-1"
    assert out[0].query == "how to handle X"


def test_log_outcome_roundtrip(tmp_path: Path) -> None:
    rec = OutcomeRecord(
        ts="2026-07-11T10:00:01",
        trowel_session_id="trowel-1",
        cc_session_id="cc-1",
        toolUseId="call_abc",
        read_id="read-1",
        memory_id="note-x",
        outcome="helpful",
        reason="changed my decision",
    )
    log_outcome(tmp_path, rec)
    out = read_outcome_log(tmp_path)
    assert len(out) == 1
    assert out[0].outcome == "helpful"
    assert out[0].memory_id == "note-x"
    assert out[0].reason == "changed my decision"


def test_corrupt_line_skipped(tmp_path: Path) -> None:
    rec = AccessRecord(
        ts="t",
        trowel_session_id="s",
        cc_session_id="c",
        toolUseId="u",
        action="read",
        search_id="s1",
        read_id="r1",
        memory_id="m1",
    )
    log_access(tmp_path, rec)
    bad = tmp_path / "meta" / "access-log.jsonl"
    with bad.open("a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    log_access(
        tmp_path,
        AccessRecord(
            ts="t2",
            trowel_session_id="s",
            cc_session_id="c",
            toolUseId="u2",
            action="read",
            search_id="s2",
            read_id="r2",
            memory_id="m2",
        ),
    )
    out = read_access_log(tmp_path)
    assert len(out) == 2


def test_read_empty_when_absent(tmp_path: Path) -> None:
    assert read_access_log(tmp_path) == []
    assert read_outcome_log(tmp_path) == []


def test_host_neutral_identity_roundtrip(tmp_path: Path) -> None:
    codex_rec = AccessRecord(
        ts="2026-07-20T10:00:00",
        trowel_session_id="trowel-codex-1",
        cc_session_id="",
        toolUseId="",
        action="search",
        search_id="search-1",
        query="note lookup",
        host_kind="codex",
        native_session_id="codex-thread-abc",
    )
    cc_rec = AccessRecord(
        ts="2026-07-20T10:00:01",
        trowel_session_id="trowel-cc-1",
        cc_session_id="cc-sid-1",
        toolUseId="call_xyz",
        action="read",
        search_id="search-1",
        read_id="read-1",
        memory_id="note-x",
        host_kind="cc",
        native_session_id="cc-sid-1",
    )
    log_access(tmp_path, codex_rec)
    log_access(tmp_path, cc_rec)
    out = read_access_log(tmp_path)
    assert len(out) == 2
    codex, cc = out
    assert codex.host_kind == "codex"
    assert codex.native_session_id == "codex-thread-abc"
    assert codex.cc_session_id == ""
    assert cc.host_kind == "cc"
    assert cc.native_session_id == "cc-sid-1"
    assert cc.cc_session_id == "cc-sid-1"


def test_pre078_log_line_reads_back_with_empty_host_fields(
    tmp_path: Path,
) -> None:
    access = tmp_path / "meta" / "access-log.jsonl"
    access.parent.mkdir(parents=True)
    access.write_text(
        '{"ts":"t","trowel_session_id":"s","cc_session_id":"c","toolUseId":"u",'
        '"action":"search","search_id":"s1","query":"q"}\n',
        encoding="utf-8",
    )
    out = read_access_log(tmp_path)
    assert len(out) == 1
    assert out[0].host_kind == ""
    assert out[0].native_session_id == ""
    assert out[0].cc_session_id == "c"


def test_outcome_host_neutral_identity_roundtrip(tmp_path: Path) -> None:
    codex_rec = OutcomeRecord(
        ts="2026-07-20T10:00:02",
        trowel_session_id="trowel-codex-1",
        cc_session_id="",
        toolUseId="",
        read_id="read-1",
        memory_id="note-x",
        outcome="helpful",
        host_kind="codex",
        native_session_id="codex-thread-abc",
    )
    cc_rec = OutcomeRecord(
        ts="2026-07-20T10:00:03",
        trowel_session_id="trowel-cc-1",
        cc_session_id="cc-sid-1",
        toolUseId="call_xyz",
        read_id="read-2",
        memory_id="note-y",
        outcome="unused",
        host_kind="cc",
        native_session_id="cc-sid-1",
    )
    log_outcome(tmp_path, codex_rec)
    log_outcome(tmp_path, cc_rec)
    out = read_outcome_log(tmp_path)
    assert len(out) == 2
    codex, cc = out
    assert codex.host_kind == "codex"
    assert codex.native_session_id == "codex-thread-abc"
    assert cc.host_kind == "cc"
    assert cc.native_session_id == "cc-sid-1"


def test_pre078_outcome_line_reads_back_with_empty_host_fields(
    tmp_path: Path,
) -> None:
    outcome = tmp_path / "meta" / "outcome-log.jsonl"
    outcome.parent.mkdir(parents=True)
    outcome.write_text(
        '{"ts":"t","trowel_session_id":"s","cc_session_id":"c",'
        '"toolUseId":"u","read_id":"r1","memory_id":"m1","outcome":"helpful"}\n',
        encoding="utf-8",
    )
    out = read_outcome_log(tmp_path)
    assert len(out) == 1
    assert out[0].host_kind == ""
    assert out[0].native_session_id == ""
    assert out[0].outcome == "helpful"
