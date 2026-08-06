"""用统一 prompt、Agent 和门禁处理一个 Profile 来源。"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from trowel_py.profile.distill.agent import (
    HostFactory,
    _ensure_distill_workdir,
    drive_and_gate,
)
from trowel_py.profile.distill.prompt import build_source_distill_prompt
from trowel_py.profile.distill.sources.evidence import (
    build_target_evidence_validator,
)
from trowel_py.profile.distill.sources.models import ProfileDistillSource
from trowel_py.profile.models import Suggestion
from trowel_py.profile.repository import ProfileRepository
from trowel_py.profile.suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    load_suggestions,
)

logger = logging.getLogger(__name__)


def _source_workdir_name(source: ProfileDistillSource) -> str:
    """返回不把 Codex 冒号身份直接用作目录名的稳定名称。"""
    if source.runtime == "claude_code":
        return source.source_id
    digest = hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()[:20]
    return f"{source.runtime}-{digest}"


async def process_profile_source(
    source: ProfileDistillSource,
    date_str: str,
    memory_root: Path,
    *,
    proxy_base_url: str,
    settings_path: Path | str | None = None,
    host_factory: HostFactory | None = None,
) -> list[Suggestion]:
    """提炼一个运行时无关来源，并只用当前策略建议做去重。"""
    repository = ProfileRepository(memory_root)
    try:
        all_suggestions = load_suggestions(memory_root)
    except ValueError:
        logger.warning("distill: corrupt suggestion queue; deduping against empty")
        all_suggestions = []
    existing = [
        suggestion
        for suggestion in all_suggestions
        if suggestion.policy_version == PROFILE_DISTILL_POLICY_VERSION
    ]
    prompt = build_source_distill_prompt(
        source,
        existing,
        repository.load_profile(),
    )
    evidence_validator = build_target_evidence_validator(source)

    base_workdir = _ensure_distill_workdir(date_str, memory_root)
    workdir = base_workdir / _source_workdir_name(source)
    workdir.mkdir(parents=True, exist_ok=True)

    gated = await drive_and_gate(
        source.source_id,
        workdir,
        prompt,
        proxy_base_url=proxy_base_url,
        settings_path=settings_path,
        host_factory=host_factory,
        date_str=date_str,
        evidence_validator=evidence_validator,
    )
    logger.info(
        "distill gate %s: %s",
        source.source_id,
        gated.stats.to_log_dict(),
    )
    return list(gated.accepted)
