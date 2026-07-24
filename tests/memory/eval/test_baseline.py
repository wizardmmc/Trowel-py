from __future__ import annotations

import logging
from pathlib import Path

from trowel_py.memory import baseline
from trowel_py.memory.eval import EvalQuery


def test_baseline_isolates_query_failure_and_writes_report(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    corpus = tmp_path / "pages"
    dictionary = tmp_path / "dictionary-L0.md"
    query_set = tmp_path / "queries.yaml"
    report_path = tmp_path / "reports" / "baseline.md"
    provider = object()
    queries = [
        EvalQuery("ok", "成功 query", frozenset({"note-a"})),
        EvalQuery("bad", "失败 query", frozenset({"note-b"})),
    ]

    class FakeRetriever:
        def __init__(self, actual_provider: object) -> None:
            assert actual_provider is provider

        def __call__(
            self, query: str, *, corpus_dir: Path, dictionary_path: Path
        ) -> list[str]:
            assert corpus_dir == corpus
            assert dictionary_path == dictionary
            if query == "失败 query":
                raise RuntimeError("offline failure")
            return ["note-a"]

    monkeypatch.setattr(baseline, "load_llm_config", lambda: object())
    monkeypatch.setattr(baseline, "build_provider", lambda _cfg: provider)
    monkeypatch.setattr(baseline, "LLMRetriever", FakeRetriever)
    monkeypatch.setattr(baseline, "load_queries", lambda path: queries)

    with caplog.at_level(logging.WARNING, logger=baseline.__name__):
        report = baseline.run_v0_baseline(
            corpus,
            dictionary,
            query_set,
            report_path,
            max_workers=2,
        )

    by_id = {result.query_id: result for result in report.results}
    assert by_id["ok"].retrieved == ("note-a",)
    assert by_id["ok"].failure == "none"
    assert by_id["bad"].retrieved == ()
    assert by_id["bad"].failure == "total-miss"
    assert "retrieval failed for bad: offline failure" in caplog.text
    assert "### bad — total-miss" in report_path.read_text(encoding="utf-8")
