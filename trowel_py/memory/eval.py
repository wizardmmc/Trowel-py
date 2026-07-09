"""offline retrieval eval harness (slice-038).

Generalizes the S1 spike into a repeatable tool. Given a corpus, a dictionary,
and a versioned ground-truth query set, run an injected retriever over each
query and compute precision/recall + a mechanical failure class per query.

The retriever is injected (``Retriever`` protocol) so unit tests pass a mock
and the real v0-baseline run passes a fresh-context LLM retriever (slice T7).
This module does NO LLM calls itself.

Mechanical failure classes (none / partial / total-miss) are computed from the
retrieved-vs-relevant sets. The deeper S1 semantic attribution (cross-domain
misroute, big-domain dilution, cold, concept) is applied at analysis time over
the per-query results — it needs domain metadata the harness does not assume.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import yaml

from trowel_py.memory import metrics

Failure = Literal["none", "partial", "total-miss"]


class Retriever(Protocol):
    """Fetch the note ids a fresh-context agent opened for one query.

    The retriever must NOT be handed the answer; it only sees the dictionary
    path and navigates L0 -> L1 -> note bodies itself (S1 anti-cheat rule).
    """

    def __call__(self, query: str, *, corpus_dir: Path,
                 dictionary_path: Path) -> Sequence[str]: ...


@dataclass(frozen=True)
class EvalQuery:
    """One ground-truth retrieval question.

    Attributes:
        query_id: stable id (for trend comparison across runs).
        query: the natural-language question.
        relevant: 2-5 note ids that SHOULD be retrieved (multi-relevant so
            recall can be measured independently of precision).
    """

    query_id: str
    query: str
    relevant: frozenset[str]


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    query: str
    retrieved: tuple[str, ...]
    relevant: frozenset[str]
    precision: float
    recall: float
    failure: Failure


@dataclass(frozen=True)
class EvalReport:
    results: tuple[QueryResult, ...]
    mean_precision: float
    mean_recall: float

    def failure_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.failure] = counts.get(r.failure, 0) + 1
        return counts


def run_eval(corpus_dir: Path | str, dictionary_path: Path | str,
             queries: Sequence[EvalQuery], retriever: Retriever) -> EvalReport:
    """Run ``retriever`` over every query; return precision/recall + failures.

    Args:
        corpus_dir: directory holding the note bodies to retrieve from.
        dictionary_path: path to the L0 dictionary the retriever starts from.
        queries: the versioned ground-truth query set (C-8: stable benchmark).
        retriever: an injected fresh-context retriever (mock in tests).

    Returns:
        An EvalReport with per-query results and means (0.0 when no queries).
    """
    corpus = Path(corpus_dir)
    dictionary = Path(dictionary_path)
    results: list[QueryResult] = []
    for q in queries:
        retrieved = tuple(retriever(q.query, corpus_dir=corpus, dictionary_path=dictionary))
        rel = set(q.relevant)
        p = metrics.precision(retrieved, rel)
        r = metrics.recall(retrieved, rel)
        results.append(QueryResult(
            query_id=q.query_id, query=q.query, retrieved=retrieved,
            relevant=frozenset(rel), precision=p, recall=r, failure=_classify(r),
        ))
    mean_p = sum(x.precision for x in results) / len(results) if results else 0.0
    mean_r = sum(x.recall for x in results) / len(results) if results else 0.0
    return EvalReport(tuple(results), mean_p, mean_r)


def load_queries(path: Path | str) -> list[EvalQuery]:
    """Load a ground-truth query set from YAML (C-8 versioned fixture).

    Expected shape::

        - query_id: q01
          query: "..."
          relevant: [note-a, note-b]
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"query set {path} must be a YAML list")
    out: list[EvalQuery] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"query #{i} is not a mapping")
        out.append(EvalQuery(
            query_id=str(item.get("query_id") or f"q{i:02d}"),
            query=str(item["query"]),
            relevant=frozenset(item.get("relevant") or []),
        ))
    return out


def format_report(report: EvalReport) -> str:
    """Human-readable report (used by the v0-baseline benchmark run)."""
    lines = [
        f"# eval report",
        f"queries: {len(report.results)}",
        f"mean precision: {report.mean_precision:.3f}",
        f"mean recall:    {report.mean_recall:.3f}",
        f"failure counts: {report.failure_counts()}",
        "",
        "| query_id | precision | recall | failure | query |",
        "|---|---|---|---|---|",
    ]
    for r in report.results:
        q = r.query.replace("|", "/").replace("\n", " ")[:60]
        lines.append(f"| {r.query_id} | {r.precision:.2f} | {r.recall:.2f} | {r.failure} | {q} |")
    return "\n".join(lines) + "\n"


def _classify(recall_value: float) -> Failure:
    if recall_value >= 1.0:
        return "none"
    if recall_value <= 0.0:
        return "total-miss"
    return "partial"
