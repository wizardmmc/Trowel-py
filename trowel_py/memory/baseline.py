"""并发运行离线检索基准，并把评估结果写成 Markdown 报告。

可直接运行：

    .venv/bin/python -m trowel_py.memory.baseline
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from trowel_py.config import load_llm_config
from trowel_py.llm.client import (
    AnthropicProvider,
    LLMConfig,
    LLMProvider,
    OpenAIProvider,
)
from trowel_py.memory.eval import (
    EvalQuery,
    EvalReport,
    format_report,
    load_queries,
    run_eval,
)
from trowel_py.memory.retrievers import LLMRetriever

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_DIR = Path(
    os.environ.get("TROWEL_WIKI_PAGES", str(_REPO_ROOT / "wiki" / "pages"))
)
_DICTIONARY_L0 = _REPO_ROOT / "docs" / "milestones" / "spike-s1" / "dictionary-L0.md"
_QUERY_SET = _REPO_ROOT / "tests" / "memory" / "fixtures" / "eval-queries.yaml"
_REPORT = _REPO_ROOT / "docs" / "milestones" / "m6v2-eval-v0-baseline.md"

log = logging.getLogger(__name__)


def build_provider(cfg: LLMConfig) -> LLMProvider:
    """为离线检索创建 OpenAI 或 Anthropic 模型客户端。

    Args:
        cfg: 模型供应商、模型名称和连接设置。

    Returns:
        与 ``cfg.provider`` 对应的模型客户端。
    """
    if cfg.provider == "openai":
        return OpenAIProvider(cfg)
    return AnthropicProvider(cfg)


def run_v0_baseline(
    corpus_dir: Path = _CORPUS_DIR,
    dictionary_path: Path = _DICTIONARY_L0,
    query_set: Path = _QUERY_SET,
    report_path: Path = _REPORT,
    max_workers: int = 5,
) -> EvalReport:
    """并发运行版本化查询集，并写出离线检索基准报告。

    任一查询失败时记录警告，并把该查询按空结果纳入评估，不中止其他查询。
    模型初始化、查询集加载、整体评估或报告写入失败仍会传给调用方。

    Args:
        corpus_dir: 存放待检索 Note Markdown 文件的语料目录。
        dictionary_path: 检索器使用的根 Dictionary 文件。
        query_set: 包含查询和相关 Note 标注的 YAML 文件。
        report_path: 写入 Markdown 评估报告的位置；父目录不存在时会创建。
        max_workers: 查询线程池允许的最大并发数，必须大于零。

    Returns:
        包含逐条结果及平均 precision、recall 的评估报告。
    """
    provider = build_provider(load_llm_config())
    retriever = LLMRetriever(provider)
    queries = load_queries(query_set)

    retrieved_by_query: dict[str, list[str]] = {}

    def _one(q: EvalQuery) -> tuple[str, list[str]]:
        """只把问题文本和检索路径交给检索器，并把单条异常转换为空结果。

        ``q.relevant`` 是评估答案，不会传给检索器。

        Args:
            q: 要执行的问题及其评估标注。

        Returns:
            查询文本及检索到的 Note 文件 stem；失败时 stem 列表为空。
        """
        try:
            got = retriever(
                q.query, corpus_dir=corpus_dir, dictionary_path=dictionary_path
            )
            return q.query, got
        except Exception as exc:  # noqa: BLE001 - 基准批次必须隔离单条查询失败
            log.warning("retrieval failed for %s: %s", q.query_id, exc)
            return q.query, []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in as_completed([ex.submit(_one, q) for q in queries]):
            key, val = fut.result()
            retrieved_by_query[key] = val
            log.info("retrieved %d notes for a query", len(val))

    def _memo(
        query: str, *, corpus_dir: Path, dictionary_path: Path
    ) -> list[str]:
        """把并发阶段缓存的结果交给评估器，避免再次调用检索器。

        Args:
            query: 用于查找缓存结果的查询文本。
            corpus_dir: 为满足 Retriever 接口而接收，不参与缓存查找。
            dictionary_path: 为满足 Retriever 接口而接收，不参与缓存查找。

        Returns:
            对应查询的 Note 文件 stem；没有记录时返回空列表。
        """
        return retrieved_by_query.get(query, [])

    report = run_eval(corpus_dir, dictionary_path, queries, _memo)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _render(report, queries, retrieved_by_query), encoding="utf-8"
    )
    log.info("baseline report written to %s", report_path)
    return report


def _render(
    report: EvalReport,
    queries: list[EvalQuery],
    retrieved_by_query: dict[str, list[str]],
) -> str:
    """渲染评估总览和未完全命中查询的明细。

    Args:
        report: 包含逐条指标和平均指标的评估结果。
        queries: 原始评估问题，用于在报告头部记录查询总数。
        retrieved_by_query: 已取得的查询结果映射；该参数不参与报告渲染。

    Returns:
        以换行符结尾的 Markdown 报告。
    """
    head = [
        "# milestone6-v2 离线检索 v0 baseline",
        "",
        f"> 日期: {date.today()} | 语料: wiki/pages (311 条) | dictionary: spike-s1 | "
        f"query 集: {len(queries)} 条（多相关，C-8 稳定基准）",
        "> 这是 recall/precision 的**首个数据点**，不是金标准。dictionary 每次重生成后"
        "重跑同一套基准，趋势才可比。",
        "",
        format_report(report),
        "",
        "## 失败明细（供归因分析）",
        "",
        "按机械失败类（none/partial/total-miss）列出 retrieved vs relevant；",
        "语义归因（跨域误路由/大领域稀释/冷门/概念）人工或反思 step 在此之上判断。",
        "",
    ]
    for r in report.results:
        if r.failure == "none":
            continue
        rel = sorted(r.relevant)
        got = list(r.retrieved)
        missed = sorted(set(rel) - set(got))
        head.append(
            f"### {r.query_id} — {r.failure} (P={r.precision:.2f} R={r.recall:.2f})"
        )
        head.append(f"- query: {r.query}")
        head.append(f"- retrieved: {got or '（空）'}")
        head.append(f"- relevant: {rel}")
        if missed:
            head.append(f"- 漏召: {missed}")
        head.append("")
    return "\n".join(head) + "\n"


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    rep = run_v0_baseline()
    print(f"\nmean precision = {rep.mean_precision:.3f}")
    print(f"mean recall    = {rep.mean_recall:.3f}")
    print(f"failure counts = {rep.failure_counts()}")
