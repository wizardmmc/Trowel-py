"""提供 Dictionary 一致性检查的稳定入口，并集中定义内容摘要算法和索引解析格式。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from trowel_py.memory.dictionary_check.evaluator import (
    compute_rendered_hash as _compute_rendered_hash,
)
from trowel_py.memory.dictionary_check.evaluator import (
    compute_source_hash as _compute_source_hash,
)
from trowel_py.memory.dictionary_check.evaluator import evaluate as _run_evaluation
from trowel_py.memory.dictionary_check.evaluator import parse_l0 as _parse_l0_text
from trowel_py.memory.dictionary_check.evaluator import (
    parse_l1_stems as _parse_l1_text,
)
from trowel_py.memory.dictionary_lock import dictionary_lock
from trowel_py.memory.dictionary_state import load_state
from trowel_py.memory.store import MemoryStore as MemoryStore
from trowel_py.memory.types import Note

_REPORT_KEYS = (
    "missing_active",
    "inactive_indexed",
    "missing_l1_files",
    "orphan_l1_files",
)

_DICT_L0 = "dictionary-L0.md"
_DICT_L1_DIR = "dictionary-L1"

_L1_STEM_ANCHOR_RE = re.compile(r"<!-- @stem (\S+) -->")
_L1_STEM_RE = re.compile(r"`notes/([^`]+)\.md`")
_L0_DOMAIN_RE = re.compile(r"^### (\S+?)（(\d+) 条")


def derive_active_corpus(root: Path | str) -> list[tuple[str, Note]]:
    """读取可解析且状态为 active 的 Note 及其文件 stem。

    Args:
        root: Note 所在的 Memory 根目录。

    Returns:
        由文件名 stem 和 Note 组成的列表。
    """
    store = MemoryStore(root)
    return store.load_notes_with_id({"status": "active"})


def compute_source_hash(corpus: list[tuple[str, Note]]) -> str:
    """计算 active 语料中索引相关字段的稳定摘要。

    stem、标题、摘要、标签和状态参与计算。函数按 stem 排序各条记录，并分别
    排序每条 Note 的标签；调用方应保证 stem 唯一。

    Args:
        corpus: 由 Note 文件名 stem 和内容组成的 active 语料。

    Returns:
        SHA-256 十六进制摘要的前 16 个字符。
    """
    return _compute_source_hash(corpus, digest=hashlib.sha256)


def compute_rendered_hash(l0_text: str, l1_files: dict[str, str]) -> str:
    """计算 L0 与全部 L1 原文的稳定摘要。

    L0 原文作为首段，各 L1 原文按领域名排序后依次追加，段之间用两个换行符
    拼接；领域名只决定顺序，不进入摘要。

    Args:
        l0_text: 完整 L0 文本。
        l1_files: 领域名到完整 L1 文本的映射。

    Returns:
        SHA-256 十六进制摘要的前 16 个字符。
    """
    return _compute_rendered_hash(
        l0_text,
        l1_files,
        digest=hashlib.sha256,
    )


def _parse_l0(l0_text: str) -> list[tuple[str, int]]:
    """按当前 L0 标题格式解析领域名和声明的 Note 数量。

    Args:
        l0_text: 要解析的完整 L0 文本。

    Returns:
        按文本顺序排列的 ``(领域名, 声明数量)`` 列表。
    """
    return _parse_l0_text(l0_text, domain_pattern=_L0_DOMAIN_RE)


def _parse_l1_stems(l1_text: str) -> list[str]:
    """从 L1 解析 Note 文件名 stem，优先使用 HTML anchor。

    文本中存在 anchor 时只返回 anchor 结果；完全没有 anchor 时才回退到旧版
    ``notes/<stem>.md`` 路径。

    Args:
        l1_text: 要解析的完整 L1 文本。

    Returns:
        按文本出现顺序排列的 Note 文件名 stem。
    """
    return _parse_l1_text(
        l1_text,
        anchor_pattern=_L1_STEM_ANCHOR_RE,
        legacy_pattern=_L1_STEM_RE,
    )


def _read_dictionary_files(
    root: Path,
    *,
    l0_filename: str,
    l1_dirname: str,
) -> tuple[str | None, dict[str, str]]:
    """读取磁盘上的 L0 和全部 Markdown L1 文件。

    Args:
        root: Dictionary 所在的 Memory 根目录。
        l0_filename: 相对 ``root`` 的 L0 文件名。
        l1_dirname: 相对 ``root`` 的 L1 目录名。

    Returns:
        L0 原文和以文件名 stem 为键的 L1 原文映射；文件或目录不存在时分别
        返回 None 或空映射。
    """
    l0_path = root / l0_filename
    l1_directory = root / l1_dirname
    l0_text = l0_path.read_text(encoding="utf-8") if l0_path.exists() else None
    l1_files: dict[str, str] = {}
    if l1_directory.exists():
        for path in sorted(l1_directory.glob("*.md")):
            l1_files[path.stem] = path.read_text(encoding="utf-8")
    return l0_text, l1_files


def _evaluate(
    corpus: list[tuple[str, Note]],
    l0_text: str | None,
    l1_files: dict[str, str],
    state_hash: str | None,
    *,
    state_status: str = "consistent",
    state_rendered_hash: str | None = None,
    baseline_required: bool = True,
) -> dict[str, Any]:
    """比较 active 语料、L0/L1 内容和已保存状态。

    L0 缺失时固定返回 missing：全部 active Note 记入 ``missing_active``，
    现存 L1 文件记入 ``orphan_l1_files``，两个摘要匹配字段返回 None。L0
    声明的 L1 文件缺失时则作为结构漂移参与 stale 判定。

    ``baseline_required=False`` 只让来源和渲染摘要差异不参与状态判定；结构
    问题和非 consistent 的 ``state_status`` 仍会导致 stale。
    ``state_rendered_hash=None`` 表示尚无渲染基线，按匹配处理。

    Args:
        corpus: 参与 Dictionary 构建的 active 语料。
        l0_text: L0 原文；None 表示根索引缺失。
        l1_files: 领域名到 L1 原文的映射。
        state_hash: 上次成功构建时保存的来源摘要。
        state_status: 已保存的 Dictionary 状态。
        state_rendered_hash: 上次成功发布时保存的渲染摘要。
        baseline_required: 是否要求当前内容匹配已保存摘要。

    Returns:
        状态为 missing、stale 或 consistent 的完整一致性报告。
    """
    return _run_evaluation(
        corpus,
        l0_text,
        l1_files,
        state_hash,
        state_status=state_status,
        state_rendered_hash=state_rendered_hash,
        baseline_required=baseline_required,
        source_hash=compute_source_hash,
        rendered_hash=compute_rendered_hash,
        parse_l0=_parse_l0,
        parse_l1_stems=_parse_l1_stems,
    )


def check_dictionary(root: Path | str) -> dict[str, Any]:
    """在共享锁内检查 Dictionary 与 active Note 是否一致。

    Args:
        root: 要检查的 Memory 根目录。

    Returns:
        Dictionary 结构、内容摘要和状态的一致性报告。
    """
    with dictionary_lock(root, exclusive=False):
        return _check_dictionary_locked(root)


def _check_dictionary_locked(root: Path | str) -> dict[str, Any]:
    """为已经持有 Dictionary 锁的调用方执行一致性检查。

    本函数不自行加锁，也不验证调用方是否持锁。

    Args:
        root: 要检查的 Memory 根目录。

    Returns:
        Dictionary 结构、内容摘要和状态的一致性报告。
    """
    root_path = Path(root)
    corpus = derive_active_corpus(root_path)
    l0_text, l1_files = _read_dictionary_files(
        root_path,
        l0_filename=_DICT_L0,
        l1_dirname=_DICT_L1_DIR,
    )
    state = load_state(root_path)
    return _evaluate(
        corpus,
        l0_text,
        l1_files,
        state.source_hash,
        state_status=state.status,
        state_rendered_hash=state.rendered_hash,
    )
