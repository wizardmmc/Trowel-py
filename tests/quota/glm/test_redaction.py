from __future__ import annotations

import json

import pytest

from tests.quota.glm.support import ok_snapshot
from trowel_py.quota import glm
from trowel_py.quota.glm import FetchResponse, GlmQuotaClient
from trowel_py.quota.routes import snapshot_to_wire


KEY = "LEAK-CANARY-key-never-in-output-1234"


async def test_key_only_reaches_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = ok_snapshot()
    monkeypatch.setattr(
        glm,
        "parse_glm_quota",
        lambda body, *, account_id, fetched_at: expected,
    )
    seen: list[dict[str, str]] = []

    async def fetcher(url: str, headers) -> FetchResponse:
        seen.append(dict(headers))
        return FetchResponse(200, {"code": 200})

    snapshot = await GlmQuotaClient(
        fetcher=fetcher,
        now_ms=lambda: 0,
    ).fetch("account", KEY)

    assert seen == [
        {
            "Authorization": KEY,
            "Accept": "application/json",
        }
    ]
    assert KEY not in repr(snapshot)
    assert KEY not in json.dumps(snapshot_to_wire(snapshot))
