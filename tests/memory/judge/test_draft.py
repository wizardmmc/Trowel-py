"""judge draft 的宽松解析。"""

import json

from trowel_py.memory.judge import _parse_draft


def test_parse_draft_coerces_false_and_drops_invalid_attribution() -> None:
    report = _parse_draft(
        json.dumps(
            {
                "hits": [
                    {
                        "memory_id": "note-a",
                        "used": "false",
                        "outcome": "unexpected",
                    }
                ],
                "recall_miss": [{"memory_id": "note-b", "attribution": "novelty"}],
            }
        ),
        cc_session_id="cc-1",
    )

    assert report.hits[0].used is False
    assert report.hits[0].outcome == "unknown"
    assert report.recall_miss == ()
