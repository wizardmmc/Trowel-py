"""Dictionary 一致性检查的稳定入口。"""

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
    store = MemoryStore(root)
    return store.load_notes_with_id({"status": "active"})


def compute_source_hash(corpus: list[tuple[str, Note]]) -> str:
    return _compute_source_hash(corpus, digest=hashlib.sha256)


def compute_rendered_hash(l0_text: str, l1_files: dict[str, str]) -> str:
    return _compute_rendered_hash(
        l0_text,
        l1_files,
        digest=hashlib.sha256,
    )


def _parse_l0(l0_text: str) -> list[tuple[str, int]]:
    return _parse_l0_text(l0_text, domain_pattern=_L0_DOMAIN_RE)


def _parse_l1_stems(l1_text: str) -> list[str]:
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
    """共享锁避免读取到 L0/L1 切换中的半成品。"""
    with dictionary_lock(root, exclusive=False):
        return _check_dictionary_locked(root)


def _check_dictionary_locked(root: Path | str) -> dict[str, Any]:
    """供已持有 dictionary 锁的调用方执行检查。"""
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
