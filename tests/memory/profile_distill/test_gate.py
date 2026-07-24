from __future__ import annotations

import json

import pytest

from tests.memory.profile_distill.support import draft_item, draft_json
from trowel_py.memory.profile_distill_job import DistillError, parse_and_gate_draft
from trowel_py.memory.profile_suggestions import PROFILE_DISTILL_POLICY_VERSION


def test_gate_keeps_60_drops_61() -> None:
    body60 = "字" * 60
    body61 = "字" * 61
    gated = parse_and_gate_draft(
        draft_json([draft_item(body60), draft_item(body61)]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert [suggestion.body for suggestion in gated.accepted] == [body60]
    assert gated.stats.dropped_too_long == 1
    assert gated.stats.accepted == 1


def test_gate_drops_empty_body() -> None:
    gated = parse_and_gate_draft(
        draft_json([draft_item(body="   "), draft_item(body="有结论")]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert len(gated.accepted) == 1
    assert gated.stats.dropped_empty_body == 1


def test_gate_drops_empty_sources() -> None:
    gated = parse_and_gate_draft(
        draft_json([draft_item(body="有结论", sources=[])]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.dropped_no_evidence == 1


def test_gate_drops_non_list_sources() -> None:
    gated = parse_and_gate_draft(
        draft_json([draft_item(body="有结论", sources="用户原话")]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.dropped_no_evidence == 1


def test_gate_drops_session_id_only_sources() -> None:
    gated = parse_and_gate_draft(
        draft_json([draft_item(body="有结论", sources=["cc1"])]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.dropped_no_evidence == 1


def test_gate_caps_at_two_records_over_limit() -> None:
    gated = parse_and_gate_draft(
        draft_json(
            [
                draft_item(body="一"),
                draft_item(body="二"),
                draft_item(body="三"),
            ]
        ),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert [suggestion.body for suggestion in gated.accepted] == ["一", "二"]
    assert gated.stats.over_limit == 1
    assert gated.stats.accepted == 2
    assert gated.stats.raw == 3


def test_gate_stamps_policy_version_2() -> None:
    gated = parse_and_gate_draft(
        draft_json([draft_item()]),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted[0].policy_version == PROFILE_DISTILL_POLICY_VERSION
    assert gated.accepted[0].status == "pending"
    assert gated.accepted[0].date == "2026-07-17"
    assert gated.accepted[0].id


def test_gate_unknown_dimension_raises() -> None:
    with pytest.raises(DistillError):
        parse_and_gate_draft(
            draft_json([draft_item(dim="personality")]),
            cc_session_id="cc1",
            date_str="2026-07-17",
        )


def test_gate_bad_json_raises() -> None:
    with pytest.raises(DistillError):
        parse_and_gate_draft(
            "{not json",
            cc_session_id="cc1",
            date_str="2026-07-17",
        )


def test_gate_non_list_suggestions_raises() -> None:
    with pytest.raises(DistillError):
        parse_and_gate_draft(
            json.dumps({"suggestions": {"dimension": "ability"}}),
            cc_session_id="cc1",
            date_str="2026-07-17",
        )


def test_gate_all_dropped_returns_empty_no_raise() -> None:
    gated = parse_and_gate_draft(
        draft_json(
            [
                draft_item(body=""),
                draft_item(body="x" * 61),
                draft_item(sources=[]),
            ]
        ),
        cc_session_id="cc1",
        date_str="2026-07-17",
    )
    assert gated.accepted == ()
    assert gated.stats.accepted == 0
    assert gated.stats.raw == 3
    assert gated.stats.dropped_empty_body == 1
    assert gated.stats.dropped_too_long == 1
    assert gated.stats.dropped_no_evidence == 1
