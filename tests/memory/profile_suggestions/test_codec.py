from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.profile_suggestions.support import V1_SHAPE, write_raw_queue
from trowel_py.memory.profile_suggestions import load_suggestions


def test_load_bad_status_value_raises(tmp_path: Path) -> None:
    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        json.dumps(
            {
                "suggestions": [
                    {
                        "id": "s1",
                        "dimension": "ability",
                        "body": "x",
                        "sources": [],
                        "date": "2026-07-14",
                        "status": "frozen",
                    }
                ],
                "updated": "2026-07-15",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_suggestions(tmp_path)


def test_v1_record_without_policy_version_loads_as_1(
    tmp_path: Path,
) -> None:
    write_raw_queue(tmp_path, [V1_SHAPE])
    [loaded] = load_suggestions(tmp_path)
    assert loaded.policy_version == 1


def test_status_defaults_to_pending(tmp_path: Path) -> None:
    raw = dict(V1_SHAPE)
    raw.pop("status")
    write_raw_queue(tmp_path, [raw])
    [loaded] = load_suggestions(tmp_path)
    assert loaded.status == "pending"


def test_v1_record_on_disk_not_rewritten(tmp_path: Path) -> None:
    payload = write_raw_queue(tmp_path, [V1_SHAPE])
    load_suggestions(tmp_path)
    assert (tmp_path / "meta" / "profile-suggestions.json").read_text(
        encoding="utf-8"
    ) == payload


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (True, 1),
        ("3", 3),
        ("bad", 1),
    ],
)
def test_policy_version_compatibility_values(
    tmp_path: Path,
    raw_value: object,
    expected: int,
) -> None:
    write_raw_queue(tmp_path, [{**V1_SHAPE, "policy_version": raw_value}])
    [loaded] = load_suggestions(tmp_path)
    assert loaded.policy_version == expected


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (
            {"id": "s", "dimension": "bad"},
            "unknown dimension 'bad' in suggestion queue",
        ),
        (
            {"id": "s", "dimension": "ability", "status": "frozen"},
            "unknown status 'frozen' in suggestion queue",
        ),
        (
            {"dimension": "ability"},
            "suggestion missing id in queue",
        ),
    ],
)
def test_codec_errors_keep_messages(
    tmp_path: Path,
    record: dict[str, object],
    message: str,
) -> None:
    write_raw_queue(tmp_path, [record])
    with pytest.raises(ValueError) as raised:
        load_suggestions(tmp_path)
    assert str(raised.value) == message
