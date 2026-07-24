from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from trowel_py.memory.profile_suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
)
from trowel_py.memory.types import (
    ProfileDimension,
    Suggestion,
    SuggestionStatus,
)


def suggestion(
    id_: str = "s1",
    dimension: str = "ability",
    body: str = "能够维护服务接口",
    sources: tuple[str, ...] = ("2026-07-14 cc_session_abc",),
    date: str = "2026-07-14",
    status: str = "pending",
    policy_version: int = PROFILE_DISTILL_POLICY_VERSION,
) -> Suggestion:
    return Suggestion(
        id=id_,
        dimension=cast(ProfileDimension, dimension),
        body=body,
        sources=sources,
        date=date,
        status=cast(SuggestionStatus, status),
        policy_version=policy_version,
    )


def write_raw_queue(tmp_path: Path, suggestions: list[object]) -> str:
    (tmp_path / "meta").mkdir(exist_ok=True)
    payload = json.dumps(
        {"suggestions": suggestions, "updated": "2026-07-15"},
        ensure_ascii=False,
    )
    (tmp_path / "meta" / "profile-suggestions.json").write_text(
        payload,
        encoding="utf-8",
    )
    return payload


V1_SHAPE = {
    "id": "v1-1",
    "dimension": "ability",
    "body": "一条较长的旧版能力描述",
    "sources": ["session-old"],
    "date": "2026-07-10",
    "status": "pending",
}
