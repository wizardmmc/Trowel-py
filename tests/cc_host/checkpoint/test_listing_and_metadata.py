from pathlib import Path

from trowel_py.cc_host import checkpoint
from trowel_py.cc_host.checkpoint import CheckpointMeta

from .support import new_turn_id


def test_list_checkpoints_roundtrip(git_repo: Path) -> None:
    turn_ids = [new_turn_id() for _ in range(3)]
    for turn_id in turn_ids:
        checkpoint.save(str(git_repo), turn_id)

    listed = checkpoint.list_checkpoints(str(git_repo))

    assert {meta.turn_id for meta in listed} == set(turn_ids)


def test_list_checkpoints_empty_when_no_refs(git_repo: Path) -> None:
    assert checkpoint.list_checkpoints(str(git_repo)) == []


def test_gc_keeps_n_newest(git_repo: Path) -> None:
    for _ in range(60):
        checkpoint.save(str(git_repo), new_turn_id())

    deleted = checkpoint.gc(str(git_repo), keep=50)

    assert deleted == 10
    assert len(checkpoint.list_checkpoints(str(git_repo))) == 50


def test_gc_noop_when_under_keep(git_repo: Path) -> None:
    for _ in range(3):
        checkpoint.save(str(git_repo), new_turn_id())

    assert checkpoint.gc(str(git_repo), keep=50) == 0
    assert len(checkpoint.list_checkpoints(str(git_repo))) == 3


def test_message_roundtrip_all_fields() -> None:
    meta = CheckpointMeta(
        turn_id="abc",
        cc_session_id="sess",
        jsonl_offset=2048,
        created_at="2026-07-04T12:00:00.000000+00:00",
    )

    parsed = checkpoint._decode_message(
        checkpoint._encode_message(meta),
        turn_id="abc",
    )

    assert parsed == meta


def test_message_roundtrip_minimal_no_session() -> None:
    meta = CheckpointMeta(
        turn_id="xyz",
        cc_session_id=None,
        jsonl_offset=None,
        created_at="2026-07-04T12:00:00.000000+00:00",
    )

    parsed = checkpoint._decode_message(
        checkpoint._encode_message(meta),
        turn_id="xyz",
    )

    assert parsed == meta


def test_decode_message_tolerates_malformed_and_legacy_fields() -> None:
    parsed = checkpoint._decode_message(
        "\n".join(
            [
                "checkpoint:abc",
                "cc-session sess",
                "jsonl-offset not-an-int",
                "jsonl-path /legacy/machine/session.jsonl",
                "created 2026-07-04T12:00:00.000000+00:00",
            ]
        ),
        turn_id="abc",
    )

    assert parsed == CheckpointMeta(
        turn_id="abc",
        cc_session_id="sess",
        jsonl_offset=None,
        created_at="2026-07-04T12:00:00.000000+00:00",
    )


def test_truncate_file_scans_back_to_newline_on_partial_line(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(b"abc\ndef\nmore\n")

    checkpoint._truncate_file(str(transcript), 6)

    assert transcript.read_bytes() == b"abc\n"


def test_truncate_file_noop_when_offset_past_eof(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_bytes(b"abc\n")

    checkpoint._truncate_file(str(transcript), 100)

    assert transcript.read_bytes() == b"abc\n"
