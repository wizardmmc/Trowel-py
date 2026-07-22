"""Criterion 7: the api key never lands in a snapshot, event, or wire payload.

The GLM Coding Plan key is a credential. It must reach the provider only via
the ``Authorization`` header and appear nowhere the read model, the journal or
the frontend could persist or display it. This test asserts the invariant with
a distinctive canary key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.quota.glm import FetchResponse, GlmQuotaClient, parse_glm_quota
from trowel_py.quota.routes import snapshot_to_wire

FIXTURES = Path(__file__).parent / "fixtures"
_REAL_PATH = FIXTURES / "glm_quota_real.json"
pytestmark = pytest.mark.skipif(
    not _REAL_PATH.exists(),
    reason="local GLM recording is gitignored; not present on a fresh clone",
)


def _real() -> dict:
    return json.loads(_REAL_PATH.read_text(encoding="utf-8"))


#: a distinctive canary so a substring search is unambiguous. Fully synthetic
#: (no real key material) — used only to prove the key never leaks.
KEY = "LEAK-CANARY-key-never-in-output-1234"


async def test_glm_key_only_in_auth_header_not_in_snapshot_or_wire() -> None:
    seen: list[dict[str, str]] = []

    async def fetcher(url: str, headers) -> FetchResponse:
        seen.append(dict(headers))
        return FetchResponse(200, _real())

    client = GlmQuotaClient(
        "https://open.bigmodel.cn", fetcher=fetcher, now_ms=lambda: 0
    )
    snapshot = await client.fetch("glm-a", KEY)

    # sanity: the key DID reach the provider via the raw auth header
    assert seen[0]["Authorization"] == KEY
    # and nowhere else: not in the snapshot repr, the wire payload, or raw
    assert KEY not in repr(snapshot)
    assert KEY not in json.dumps(snapshot_to_wire(snapshot))
    for window in snapshot.windows:
        assert KEY not in json.dumps(dict(window.raw))


def test_parse_glm_quota_output_carries_no_key() -> None:
    snapshot = parse_glm_quota(_real(), account_id="glm-a", fetched_at=0)
    assert KEY not in repr(snapshot)
    assert KEY not in json.dumps(snapshot_to_wire(snapshot))
