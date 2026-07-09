"""quality guard for the multi-relevant eval query set (slice-038 T6, C-8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.eval import load_queries

FIXTURE = Path(__file__).parent / "fixtures" / "eval-queries.yaml"
WIKI_PAGES = Path("/Users/hamxf/VirtualVolumn/ClaudeDesktop/wiki/pages")
HAS_WIKI = WIKI_PAGES.exists()


@pytest.fixture(scope="module")
def queries():
    return load_queries(FIXTURE)


def test_fixture_has_enough_queries(queries) -> None:
    assert len(queries) >= 20, f"expected >=20 queries, got {len(queries)}"


def test_every_query_is_multi_relevant(queries) -> None:
    # the whole point: recall can only be measured independently when each
    # query has 2+ relevant notes (S1's single-relevant set collapsed the two).
    for q in queries:
        assert 2 <= len(q.relevant) <= 5, (
            f"{q.query_id}: relevant must be 2-5 notes, got {len(q.relevant)}"
        )


def test_query_ids_unique(queries) -> None:
    ids = [q.query_id for q in queries]
    assert len(ids) == len(set(ids)), "duplicate query_id"


@pytest.mark.skipif(not HAS_WIKI, reason="wiki/pages corpus not present")
def test_every_relevant_stem_exists_in_wiki(queries) -> None:
    # the benchmark is only valid if every relevant id is a real retrievable note.
    stems = {p.stem for p in WIKI_PAGES.glob("*.md")}
    missing: dict[str, set[str]] = {}
    for q in queries:
        absent = q.relevant - stems
        if absent:
            missing[q.query_id] = absent
    assert not missing, f"relevant stems not found in wiki/pages: {missing}"


def test_fixture_covers_cross_domain_and_concept(queries) -> None:
    # the raw YAML carries a `category` tag; prove the set deliberately includes
    # the S1 failure modes (cross-domain / small-domain / concept), not just
    # easy in-domain hits.
    import yaml
    raw = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    cats = {item.get("category") for item in raw}
    assert "cross-domain" in cats
    assert "concept" in cats
