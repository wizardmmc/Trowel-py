"""运行离线检索评估，并生成每条查询的指标和机械失败分类。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import yaml

from trowel_py.memory import metrics

Failure = Literal["none", "partial", "total-miss"]


class Retriever(Protocol):
    """约束只接收问题和检索路径的离线检索器接口。"""

    def __call__(
        self, query: str, *, corpus_dir: Path, dictionary_path: Path
    ) -> Sequence[str]:
        """检索与查询相关的 Note 文件 stem。

        Args:
            query: 用于检索的自然语言问题。
            corpus_dir: 存放 Note Markdown 文件的语料目录。
            dictionary_path: 检索使用的根 Dictionary 文件。

        Returns:
            检索到的 Note 文件 stem，顺序由具体检索器决定。
        """
        ...


@dataclass(frozen=True)
class EvalQuery:
    """记录一条评估问题及其预先标注的相关 Note。

    Attributes:
        query_id: 在评估集和报告中标识该问题的 ID。
        query: 交给检索器的自然语言问题。
        relevant: 人工标注为相关的 Note 文件 stem 集合。
    """

    query_id: str
    query: str
    relevant: frozenset[str]


@dataclass(frozen=True)
class QueryResult:
    """记录单条评估问题的检索结果、指标和机械失败级别。

    Attributes:
        query_id: 对应评估问题的 ID。
        query: 本次检索使用的自然语言问题。
        retrieved: 检索器返回的 Note 文件 stem，保留原始顺序。
        relevant: 该问题预先标注的相关 Note 文件 stem 集合。
        precision: 按 stem 集合计算的 precision；重复检索结果只计一次。
        recall: 按 stem 集合计算的 recall；没有相关项时为 0.0。
        failure: 仅按 recall 判定的失败级别；1.0 为 ``none``，0.0 为
            ``total-miss``，两者之间为 ``partial``。
    """

    query_id: str
    query: str
    retrieved: tuple[str, ...]
    relevant: frozenset[str]
    precision: float
    recall: float
    failure: Failure


@dataclass(frozen=True)
class EvalReport:
    """汇总一组查询结果及其平均 precision 和 recall。

    Attributes:
        results: 按输入查询顺序排列的逐条结果。
        mean_precision: 所有逐条 precision 的算术平均值；没有查询时为 0.0。
        mean_recall: 所有逐条 recall 的算术平均值；没有查询时为 0.0。
    """

    results: tuple[QueryResult, ...]
    mean_precision: float
    mean_recall: float

    def failure_counts(self) -> dict[str, int]:
        """按各级别首次出现的顺序统计查询数，省略计数为零的级别。"""
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
    """逐条运行检索器，并按预先标注的相关项计算评估结果。

    相关项标注不会传给检索器。指标比较时忽略返回结果的顺序和重复项，但报告
    仍保留检索器的原始顺序；两个均值按查询等权计算，不按相关项数量加权。检索
    器异常直接向上传播，空查询集的两个均值均为 0.0。

    Args:
        corpus_dir: 传给检索器的 Note 语料目录。
        dictionary_path: 传给检索器的根 Dictionary 文件。
        queries: 按评估顺序排列的问题及相关项标注。
        retriever: 只根据问题、语料和 Dictionary 返回 Note stem 的检索器。

    Returns:
        包含逐条结果和平均指标的评估报告。
    """
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
    """从 YAML 文件读取带相关项标注的评估问题集。

    文件顶层必须是列表，每项必须是映射并包含 ``query``；其值及非假值的
    ``query_id`` 都会转换为字符串。``query_id`` 缺失或为假值时按从零开始
    的列表位置生成；``relevant`` 缺失或为假值时使用空集合，否则直接构造
    ``frozenset``，不会转换其中的元素，字符串因此会按字符拆分。未识别字段
    会被忽略，查询 ID 的唯一性也不在此处检查。例如：

        - query_id: q01
          query: "..."
          relevant: [note-a, note-b]

    Args:
        path: 评估问题集的 YAML 文件路径。

    Returns:
        按 YAML 列表顺序解析的问题。

    Raises:
        OSError: 文件无法读取。
        UnicodeError: 文件不是有效的 UTF-8。
        yaml.YAMLError: YAML 无法解析。
        ValueError: YAML 顶层不是列表，或其中一项不是映射。
        KeyError: 某项缺少 ``query``。
        TypeError: 非空 ``relevant`` 无法构造成集合。
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
    """把评估汇总和逐条指标渲染为 Markdown 表格。

    汇总指标保留三位小数，逐条指标保留两位小数。表格中的问题会把 ``|`` 和
    换行分别替换为 ``/`` 和空格，再截取前 60 个字符；查询 ID 不做转义。

    Args:
        report: 要渲染的离线检索评估报告。

    Returns:
        以换行符结尾的 Markdown 文本。
    """
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
    """将 recall 大于等于 1 归为无失败，小于等于 0 归为完全漏检。

    其余值（包括 NaN）归为部分命中。
    """
    if recall_value >= 1.0:
        return "none"
    if recall_value <= 0.0:
        return "total-miss"
    return "partial"
