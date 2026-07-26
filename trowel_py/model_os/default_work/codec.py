"""Default generation 输出的严格解码与精确主张归一化。"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping

from .models import CandidateDraft, DefaultWorkError

_FIELDS = (
    "idea",
    "source_refs",
    "related_question",
    "why_useful",
    "verification",
    "uncertainty",
)


def normalized_claim_hash(content: str) -> str:
    normalized = " ".join(
        unicodedata.normalize("NFKC", content).casefold().strip().split()
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_candidate_output(
    raw: str, allowed_refs: set[str]
) -> tuple[CandidateDraft, ...]:
    """只接受完整 JSON object；不从 markdown 或解释文字中猜 JSON。"""

    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise DefaultWorkError("output_schema_invalid") from exc
    if not isinstance(decoded, Mapping) or set(decoded) != {"candidates"}:
        raise DefaultWorkError("output_schema_invalid")
    values = decoded["candidates"]
    if not isinstance(values, list) or len(values) > 2:
        raise DefaultWorkError("output_schema_invalid")
    drafts: list[CandidateDraft] = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != set(_FIELDS):
            raise DefaultWorkError("output_schema_invalid")
        refs = value["source_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or len(refs) != len(set(refs))
            or any(not isinstance(ref, str) or ref not in allowed_refs for ref in refs)
        ):
            raise DefaultWorkError("output_schema_invalid")
        strings = {key: value[key] for key in _FIELDS if key != "source_refs"}
        if any(
            not isinstance(item, str) or not item.strip() for item in strings.values()
        ):
            raise DefaultWorkError("output_schema_invalid")
        drafts.append(
            CandidateDraft(
                content=strings["idea"].strip(),
                source_refs=tuple(refs),
                related_question=strings["related_question"].strip(),
                why_useful=strings["why_useful"].strip(),
                verification=strings["verification"].strip(),
                uncertainty=strings["uncertainty"].strip(),
            )
        )
    return tuple(drafts)
