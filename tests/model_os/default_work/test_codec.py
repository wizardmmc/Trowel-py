import pytest

from trowel_py.model_os.default_work import (
    DefaultWorkError,
    normalized_claim_hash,
    parse_candidate_output,
)


def test_parser_accepts_zero_to_two_candidates() -> None:
    assert parse_candidate_output('{"candidates": []}', {"memory://notes/a"}) == ()
    drafts = parse_candidate_output(
        '{"candidates":[{"idea":"A useful link",'
        '"source_refs":["memory://notes/a"],'
        '"related_question":"What changes?",'
        '"why_useful":"It joins two checks",'
        '"verification":"Run both checks",'
        '"uncertainty":"Only one platform observed"}]}',
        {"memory://notes/a"},
    )
    assert drafts[0].content == "A useful link"


@pytest.mark.parametrize(
    "raw",
    [
        'prefix {"candidates":[]}',
        '{"candidates":[]} suffix',
        '{"candidates":[{}, {}, {}]}',
        '{"candidates":[{"idea":"x","source_refs":["memory://notes/b"],'
        '"related_question":"q","why_useful":"w","verification":"v",'
        '"uncertainty":"u"}]}',
    ],
)
def test_parser_rejects_explanations_schema_errors_and_unknown_refs(raw) -> None:
    with pytest.raises(DefaultWorkError) as raised:
        parse_candidate_output(raw, {"memory://notes/a"})
    assert raised.value.code == "output_schema_invalid"


def test_normalized_claim_hash_is_nfkc_casefolded_and_whitespace_collapsed() -> None:
    assert normalized_claim_hash(" Ａ  Useful\n  LINK ") == normalized_claim_hash(
        "a useful link"
    )
