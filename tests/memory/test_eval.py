"""tests for the offline retrieval eval harness (slice-038 T4)."""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.eval import (
    EvalQuery,
    _classify,
    format_report,
    load_queries,
    run_eval,
)


def _retriever_by_query(table: dict[str, list[str]]):
    def retriever(query: str, *, corpus_dir: Path, dictionary_path: Path):
        return table.get(query, [])
    return retriever


def test_run_eval_metrics_and_failures(tmp_path: Path) -> None:
    queries = [
        EvalQuery("q1", "qq1", frozenset({"a", "b"})),
        EvalQuery("q2", "qq2", frozenset({"c"})),
    ]
    retriever = _retriever_by_query({"qq1": ["a", "x"], "qq2": ["z"]})
    report = run_eval(tmp_path, tmp_path / "dict.md", queries, retriever)

    # q1: retrieved {a,x} vs relevant {a,b} -> precision 1/2, recall 1/2, partial
    assert report.results[0].precision == 1 / 2
    assert report.results[0].recall == 1 / 2
    assert report.results[0].failure == "partial"
    # q2: retrieved {z} vs relevant {c} -> 0/0, total-miss
    assert report.results[1].precision == 0.0
    assert report.results[1].recall == 0.0
    assert report.results[1].failure == "total-miss"
    # means
    assert report.mean_precision == (1 / 2 + 0.0) / 2
    assert report.mean_recall == (1 / 2 + 0.0) / 2


def test_full_hit_classified_none(tmp_path: Path) -> None:
    queries = [EvalQuery("q", "qq", frozenset({"a", "b"}))]
    retriever = _retriever_by_query({"qq": ["a", "b"]})
    report = run_eval(tmp_path, tmp_path / "d", queries, retriever)
    assert report.results[0].failure == "none"
    assert report.mean_recall == 1.0


def test_empty_queries(tmp_path: Path) -> None:
    report = run_eval(tmp_path, tmp_path / "d", [], _retriever_by_query({}))
    assert report.results == ()
    assert report.mean_precision == 0.0
    assert report.mean_recall == 0.0


def test_failure_counts(tmp_path: Path) -> None:
    queries = [
        EvalQuery("q1", "qq1", frozenset({"a"})),
        EvalQuery("q2", "qq2", frozenset({"b"})),
        EvalQuery("q3", "qq3", frozenset({"c", "d"})),
    ]
    retriever = _retriever_by_query({"qq1": ["a"], "qq2": ["z"], "qq3": ["c"]})
    report = run_eval(tmp_path, tmp_path / "d", queries, retriever)
    counts = report.failure_counts()
    assert counts.get("none") == 1       # q1
    assert counts.get("total-miss") == 1  # q2
    assert counts.get("partial") == 1     # q3


def test_classify_boundaries() -> None:
    assert _classify(1.0) == "none"
    assert _classify(0.5) == "partial"
    assert _classify(0.0) == "total-miss"


def test_load_queries(tmp_path: Path) -> None:
    fixture = tmp_path / "queries.yaml"
    fixture.write_text(
        '- query_id: q01\n  query: "怎么伪造身份"\n  relevant: [id-blackmarket, prng-forgery]\n'
        '- query_id: q02\n  query: "build 不生效"\n  relevant: [browser-cache, vite-cache]\n',
        encoding="utf-8",
    )
    qs = load_queries(fixture)
    assert len(qs) == 2
    assert qs[0].query_id == "q01"
    assert qs[0].relevant == frozenset({"id-blackmarket", "prng-forgery"})
    assert qs[1].relevant == frozenset({"browser-cache", "vite-cache"})


def test_load_queries_rejects_non_list(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: value\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        load_queries(bad)


def test_format_report_contains_means() -> None:
    queries = [EvalQuery("q", "qq", frozenset({"a"}))]
    report = run_eval(Path("."), Path("d"), queries, _retriever_by_query({"qq": ["a"]}))
    text = format_report(report)
    assert "mean precision" in text
    assert "mean recall" in text
