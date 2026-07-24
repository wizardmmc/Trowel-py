"""版本化多相关 query fixture 的质量门禁。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trowel_py.memory.eval import load_queries

FIXTURE = Path(__file__).parents[1] / "fixtures" / "eval-queries.yaml"
WIKI_PAGES = Path(os.environ.get("TROWEL_WIKI_PAGES", ""))
HAS_WIKI = bool(os.environ.get("TROWEL_WIKI_PAGES")) and WIKI_PAGES.exists()


@pytest.fixture(scope="module")
def queries():
    return load_queries(FIXTURE)


def test_fixture_has_enough_queries(queries) -> None:
    assert len(queries) >= 20, f"expected >=20 queries, got {len(queries)}"


def test_every_query_is_multi_relevant(queries) -> None:
    # 每个 query 至少两个相关 note，recall 才能独立于 precision 计量。
    for q in queries:
        assert 2 <= len(q.relevant) <= 5, (
            f"{q.query_id}: relevant must be 2-5 notes, got {len(q.relevant)}"
        )


def test_query_ids_unique(queries) -> None:
    ids = [q.query_id for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query_id"


@pytest.mark.skipif(not HAS_WIKI, reason="wiki/pages corpus not present")
def test_every_relevant_stem_exists_in_wiki(queries) -> None:
    stems = {p.stem for p in WIKI_PAGES.glob("*.md")}
    missing: dict[str, set[str]] = {}
    for q in queries:
        absent = q.relevant - stems
        if absent:
            missing[q.query_id] = absent
    assert not missing, f"relevant stems not found in wiki/pages: {missing}"


def test_fixture_covers_cross_domain_and_concept(queries) -> None:
    import yaml

    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    cats = {item.get("category") for item in raw}
    assert "cross-domain" in cats
    assert "concept" in cats
