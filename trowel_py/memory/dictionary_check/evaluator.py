"""Dictionary 的纯解析、摘要与一致性评估。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Pattern, Protocol

from trowel_py.memory.types import Note


class _Hash(Protocol):
    """摘要工厂返回值必须提供的最小接口。"""

    def hexdigest(self) -> str:
        """返回十六进制摘要文本。"""
        ...


def compute_source_hash(
    corpus: list[tuple[str, Note]],
    *,
    digest: Callable[[bytes], _Hash],
) -> str:
    """计算 active 语料中索引相关字段的稳定摘要。

    每条记录由 stem、标题、摘要、排序后的标签和状态组成，记录按 stem 排序；
    调用方应保证 stem 唯一。

    Args:
        corpus: 由 Note 文件名 stem 和内容组成的 active 语料。
        digest: 接收 UTF-8 字节并返回摘要对象的工厂。

    Returns:
        十六进制摘要的前 16 个字符。
    """
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
    """计算 L0 与全部 L1 原文的稳定摘要。

    L0 原文作为首段，各 L1 原文按映射键排序后依次追加，段之间用两个换行符
    拼接；映射键只决定顺序，不进入摘要。

    Args:
        l0_text: 完整 L0 文本。
        l1_files: 领域名到完整 L1 文本的映射。
        digest: 接收 UTF-8 字节并返回摘要对象的工厂。

    Returns:
        十六进制摘要的前 16 个字符。
    """
    parts = [l0_text] + [l1_files[key] for key in sorted(l1_files)]
    return digest("\n\n".join(parts).encode("utf-8")).hexdigest()[:16]


def parse_l0(
    l0_text: str,
    *,
    domain_pattern: Any,
) -> list[tuple[str, int]]:
    """逐行匹配 L0 文本，解析领域名和声明的 Note 数量。

    Args:
        l0_text: 要逐行解析的完整 L0 文本。
        domain_pattern: 对每一行调用 ``match`` 的模式；前两个捕获组须分别
            返回领域名和可由 ``int`` 转换的数量文本。

    Returns:
        按文本顺序排列的 ``(领域名, 声明数量)`` 列表；``match`` 未命中的行
        被忽略。
    """
    domains: list[tuple[str, int]] = []
    for line in l0_text.splitlines():
        match = domain_pattern.match(line)
        if match:
            domains.append((match.group(1), int(match.group(2))))
    return domains


def parse_l1_stems(
    l1_text: str,
    *,
    anchor_pattern: Pattern[str],
    legacy_pattern: Pattern[str],
) -> list[str]:
    """从 L1 解析 Note 文件名 stem，并兼容旧路径格式。

    只要 ``anchor_pattern`` 找到结果，就完全忽略 ``legacy_pattern`` 的结果。

    Args:
        l1_text: 要解析的完整 L1 文本。
        anchor_pattern: 查找当前 HTML anchor 中 stem 的模式。
        legacy_pattern: 查找旧版 Note 路径中 stem 的模式。

    Returns:
        当前 anchor 中的 stem；不存在 anchor 时返回旧路径中的 stem。
    """
    anchored = anchor_pattern.findall(l1_text)
    if anchored:
        if all(isinstance(item, str) for item in anchored):
            return anchored
        raise ValueError("anchor pattern must contain exactly one capture group")
    legacy = legacy_pattern.findall(l1_text)
    if all(isinstance(item, str) for item in legacy):
        return legacy
    raise ValueError("legacy pattern must contain exactly one capture group")


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
    """比较 active 语料、L0/L1 内容和已保存状态。

    L0 缺失时直接报告 missing。L0 存在时，结构差异、非 consistent 状态以及
    要求基线时的来源或渲染摘要差异都会报告 stale；否则报告 consistent。

    L0 缺失时，全部 active stem 都列入 ``missing_active``，现存 L1 文件都
    列入 ``orphan_l1_files``；残留 L1 仍用于统计已索引和 inactive stem，
    两个摘要匹配字段返回 None。

    ``duplicate_entries`` 的 ``count`` 是同一 stem 在全部 L1 中的总出现次数，
    ``domains`` 是这些出现位置所属领域的排序去重列表。
    ``baseline_required`` 只决定摘要差异是否影响最终状态；结构差异和非
    consistent 状态始终参与判定。没有渲染摘要基线时按匹配处理，并跳过渲染
    摘要计算。

    Args:
        corpus: 参与 Dictionary 构建的 active 语料。
        l0_text: L0 原文；None 表示根索引缺失。
        l1_files: 领域名到 L1 原文的映射。
        state_hash: 上次成功构建时保存的来源摘要；None 会报告来源摘要不匹配。
        state_status: 已保存的 Dictionary 状态。
        state_rendered_hash: 上次成功发布时保存的渲染摘要；None 会按匹配处理。
        baseline_required: 来源和渲染摘要差异是否参与状态判定。
        source_hash: 计算当前 active 语料摘要的函数。
        rendered_hash: 计算当前 L0/L1 渲染摘要的函数。
        parse_l0: 从 L0 解析领域名和声明数量的函数。
        parse_l1_stems: 从 L1 解析 Note stem 的函数。

    Returns:
        包含状态、结构差异和摘要匹配结果的一致性报告。
    """
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
    # 无需可信基线时仍报告摘要匹配结果，只是不让差异改变最终状态。
    hash_dirty = not source_hash_matches if baseline_required else False
    state_untrusted = state_status != "consistent"
    # 没有渲染摘要基线时短路为匹配，不调用渲染摘要函数。
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
