"""对注入的 retriever 运行版本化 query，并计算检索指标与机械失败分类。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import yaml

from trowel_py.memory import metrics

Failure = Literal["none", "partial", "total-miss"]


class Retriever(Protocol):
    """Retriever 只能看到 query、语料和 dictionary，不能接收 relevant 答案。"""

    def __call__(
        self, query: str, *, corpus_dir: Path, dictionary_path: Path
    ) -> Sequence[str]: ...


@dataclass(frozen=True)
class EvalQuery:
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


def run_eval(
    corpus_dir: Path | str,
    dictionary_path: Path | str,
    queries: Sequence[EvalQuery],
    retriever: Retriever,
) -> EvalReport:
    """逐 query 评估；空 query 集的两个均值均为 0.0。"""
    corpus = Path(corpus_dir)
    dictionary = Path(dictionary_path)
    results: list[QueryResult] = []
    for q in queries:
        retrieved = tuple(
            retriever(q.query, corpus_dir=corpus, dictionary_path=dictionary)
        )
        rel = set(q.relevant)
        p = metrics.precision(retrieved, rel)
        r = metrics.recall(retrieved, rel)
        results.append(
            QueryResult(
                query_id=q.query_id,
                query=q.query,
                retrieved=retrieved,
                relevant=frozenset(rel),
                precision=p,
                recall=r,
                failure=_classify(r),
            )
        )
    mean_p = sum(x.precision for x in results) / len(results) if results else 0.0
    mean_r = sum(x.recall for x in results) / len(results) if results else 0.0
    return EvalReport(tuple(results), mean_p, mean_r)


def load_queries(path: Path | str) -> list[EvalQuery]:
    """从 YAML 读取版本化 ground-truth query 集。

    格式：

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
        out.append(
            EvalQuery(
                query_id=str(item.get("query_id") or f"q{i:02d}"),
                query=str(item["query"]),
                relevant=frozenset(item.get("relevant") or []),
            )
        )
    return out


def format_report(report: EvalReport) -> str:
    lines = [
        "# eval report",
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
        lines.append(
            f"| {r.query_id} | {r.precision:.2f} | {r.recall:.2f} | {r.failure} | {q} |"
        )
    return "\n".join(lines) + "\n"


def _classify(recall_value: float) -> Failure:
    if recall_value >= 1.0:
        return "none"
    if recall_value <= 0.0:
        return "total-miss"
    return "partial"
