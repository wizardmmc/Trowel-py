"""tests for the layer-one seed bootstrap (slice-038 T3)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory import seeds
from trowel_py.memory.store import MemoryStore


def test_bootstrap_writes_core_with_all_seeds(tmp_path: Path) -> None:
    assert seeds.bootstrap_core(tmp_path) is True
    core_text = MemoryStore(tmp_path).load_core()
    assert core_text, "core.md should not be empty"
    # every seed keyword must appear (one per imperative)
    for kw in seeds.SEED_KEYWORDS:
        assert kw in core_text, f"seed keyword missing: {kw!r}"


def test_bootstrap_is_idempotent_no_overwrite(tmp_path: Path) -> None:
    # C-5: a second bootstrap must NOT overwrite a (human-reviewed) core.
    assert seeds.bootstrap_core(tmp_path) is True
    core_path = tmp_path / "core.md"
    human_edited = "# human reviewed core\nthis must survive.\n"
    core_path.write_text(human_edited, encoding="utf-8")
    assert seeds.bootstrap_core(tmp_path) is False  # skipped
    assert core_path.read_text(encoding="utf-8") == human_edited


def test_bootstrap_has_eight_items(tmp_path: Path) -> None:
    assert len(seeds.CORE_SEED_ITEMS) == 8
