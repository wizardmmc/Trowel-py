from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trowel_py.quota.glm import FetchResponse, GlmQuotaClient
from trowel_py.quota.types import Provider, QuotaSnapshot, QuotaStatus


FIXTURES = Path(__file__).parent.parent / "fixtures"
RECORDED_PATH = FIXTURES / "glm_quota_real.json"
NOW = 1_700_000_000_000


def load_recorded() -> dict[str, Any]:
    return json.loads(RECORDED_PATH.read_text(encoding="utf-8"))


def ok_snapshot(
    account_id: str = "account",
    fetched_at: int = NOW,
) -> QuotaSnapshot:
    return QuotaSnapshot(
        provider=Provider.GLM,
        account_id=account_id,
        plan_level=None,
        windows=(),
        fetched_at=fetched_at,
        status=QuotaStatus.OK,
    )


def client_with_queue(
    queue: list[FetchResponse | BaseException],
    seen: list[tuple[str, dict[str, str]]] | None = None,
) -> GlmQuotaClient:
    async def fetcher(
        url: str,
        headers: Mapping[str, str],
    ) -> FetchResponse:
        if seen is not None:
            seen.append((url, dict(headers)))
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return GlmQuotaClient(
        "https://open.bigmodel.cn/",
        fetcher=fetcher,
        now_ms=lambda: NOW,
    )
