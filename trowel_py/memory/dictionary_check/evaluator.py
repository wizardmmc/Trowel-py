"""Dictionary 的纯解析、摘要与一致性评估。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from trowel_py.memory.types import Note


class _Hash(Protocol):
    def hexdigest(self) -> str: ...


def compute_source_hash(
    corpus: list[tuple[str, Note]],
    *,
    digest: Callable[[bytes], _Hash],
) -> str:
    rows: list[str] = []
    for stem, note in sorted(corpus, key=lambda item: item[0]):
        tags = ",".join(sorted(note.tags))
        rows.append(f"{stem}\t{note.title}\t{note.summary}\t{tags}\t{note.status}")
    return digest("\n".join(rows).encode("utf-8")).hexdigest()[:16]


def compute_rendered_hash(
    l0_text: str,
    l1_files: dict[str, str],
    *,
    digest: Callable[[bytes], _Hash],
) -> str:
    parts = [l0_text] + [l1_files[key] for key in sorted(l1_files)]
    return digest("\n\n".join(parts).encode("utf-8")).hexdigest()[:16]


def parse_l0(
    l0_text: str,
    *,
    domain_pattern: Any,
) -> list[tuple[str, int]]:
    domains: list[tuple[str, int]] = []
    for line in l0_text.splitlines():
        match = domain_pattern.match(line)
        if match:
            domains.append((match.group(1), int(match.group(2))))
    return domains


def parse_l1_stems(
    l1_text: str,
    *,
    anchor_pattern: Any,
    legacy_pattern: Any,
) -> list[str]:
    anchored = anchor_pattern.findall(l1_text)
    if anchored:
        return anchored
    return legacy_pattern.findall(l1_text)


def evaluate(
    corpus: list[tuple[str, Note]],
    l0_text: str | None,
    l1_files: dict[str, str],
    state_hash: str | None,
    *,
    state_status: str,
    state_rendered_hash: str | None,
    baseline_required: bool,
    source_hash: Callable[[list[tuple[str, Note]]], str],
    rendered_hash: Callable[[str, dict[str, str]], str],
    parse_l0: Callable[[str], list[tuple[str, int]]],
    parse_l1_stems: Callable[[str], list[str]],
) -> dict[str, Any]:
    active_stems = {stem for stem, _note in corpus}
    current_hash = source_hash(corpus)

    if l0_text is None:
        # 没有 L0 就没有可用索引；L1 只能报告为孤儿，不能抵消 active 缺失。
        indexed: set[str] = set()
        for text in l1_files.values():
            indexed |= set(parse_l1_stems(text))
        return {
            "status": "missing",
            "active_notes": len(active_stems),
            "indexed_unique": len(indexed),
            "missing_active": sorted(active_stems),
            "inactive_indexed": sorted(indexed - active_stems),
            "duplicate_entries": [],
            "missing_l1_files": [],
            "orphan_l1_files": sorted(l1_files),
            "l0_count_mismatches": [],
            "source_hash_matches": None,
            "rendered_hash_matches": None,
        }

    declared = parse_l0(l0_text)
    declared_set = {name for name, _count in declared}

    stem_domains: dict[str, list[str]] = {}
    domain_actual: dict[str, int] = {}
    on_disk_l1: set[str] = set()
    for domain, text in l1_files.items():
        on_disk_l1.add(domain)
        stems = parse_l1_stems(text)
        domain_actual[domain] = len(stems)
        for stem in stems:
            stem_domains.setdefault(stem, []).append(domain)

    indexed_stems = set(stem_domains)
    missing_active = sorted(active_stems - indexed_stems)
    inactive_indexed = sorted(indexed_stems - active_stems)
    duplicate_entries = sorted(
        (
            {
                "stem": stem,
                "count": len(domains),
                "domains": sorted(set(domains)),
            }
            for stem, domains in stem_domains.items()
            if len(domains) > 1
        ),
        key=lambda item: str(item["stem"]),
    )
    missing_l1_files = sorted(declared_set - on_disk_l1)
    orphan_l1_files = sorted(on_disk_l1 - declared_set)
    l0_count_mismatches = [
        {
            "domain": name,
            "declared": declared_count,
            "actual": domain_actual.get(name, 0),
        }
        for name, declared_count in declared
        if declared_count != domain_actual.get(name, 0)
    ]

    source_hash_matches = state_hash is not None and state_hash == current_hash
    # staging 尚未建立可信基线，因此忽略所有 hash 差异；state 状态仍具权威性。
    hash_dirty = not source_hash_matches if baseline_required else False
    state_untrusted = state_status != "consistent"
    # 首次成功发布前没有 rendered baseline，此时不把缺少基线当作内容漂移。
    rendered_hash_matches = (
        state_rendered_hash is None
        or state_rendered_hash == rendered_hash(l0_text, l1_files)
    )
    rendered_dirty = not rendered_hash_matches if baseline_required else False

    dirty = bool(
        missing_active
        or inactive_indexed
        or duplicate_entries
        or missing_l1_files
        or orphan_l1_files
        or l0_count_mismatches
        or hash_dirty
        or state_untrusted
        or rendered_dirty
    )
    return {
        "status": "stale" if dirty else "consistent",
        "active_notes": len(active_stems),
        "indexed_unique": len(indexed_stems),
        "missing_active": missing_active,
        "inactive_indexed": inactive_indexed,
        "duplicate_entries": duplicate_entries,
        "missing_l1_files": missing_l1_files,
        "orphan_l1_files": orphan_l1_files,
        "l0_count_mismatches": l0_count_mismatches,
        "source_hash_matches": source_hash_matches,
        "rendered_hash_matches": rendered_hash_matches,
    }
