"""offline v0 retrieval baseline runner (slice-038 T7).

NOT a pytest test — a one-shot benchmark. Runs the fresh-context LLM retriever
(GLM via trowel's anthropic-compatible provider) over:

- corpus:   wiki/pages (311 real notes)
- dictionary: docs/milestones/spike-s1/dictionary-L0.md (+ dictionary-L1/)
- queries:  tests/memory/fixtures/eval-queries.yaml (multi-relevant ground truth)

Writes a report to docs/milestones/m6v2-eval-v0-baseline.md. Re-run after every
dictionary regeneration to track the recall/precision trend (C-8).

Run::

    .venv/bin/python -m trowel_py.memory.baseline
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from trowel_py.config import load_llm_config
from trowel_py.llm.client import AnthropicProvider, LLMConfig, LLMProvider, OpenAIProvider
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
    """Construct the active LLM provider from config (mirrors create_llm_service)."""
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
    """Run retrievals (concurrent), compute the report, write it to disk.

    Returns the EvalReport (also used by an integration test).
    """
    provider = build_provider(load_llm_config())
    retriever = LLMRetriever(provider)
    queries = load_queries(query_set)

    # Concurrent retrieval (one memoized result per query). Failures -> empty
    # retrieval (counted as total-miss), so a transient 429 doesn't abort all.
    retrieved_by_query: dict[str, list[str]] = {}

    def _one(q: EvalQuery) -> tuple[str, list[str]]:
        try:
            got = retriever(q.query, corpus_dir=corpus_dir, dictionary_path=dictionary_path)
            return q.query, got
        except Exception as exc:  # noqa: BLE001 — benchmark must not abort on one query
            log.warning("retrieval failed for %s: %s", q.query_id, exc)
            return q.query, []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in as_completed([ex.submit(_one, q) for q in queries]):
            key, val = fut.result()
            retrieved_by_query[key] = val
            log.info("retrieved %d notes for a query", len(val))

    def _memo(query: str, *, corpus_dir, dictionary_path) -> list[str]:
        return retrieved_by_query.get(query, [])

    report = run_eval(corpus_dir, dictionary_path, queries, _memo)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render(report, queries, retrieved_by_query), encoding="utf-8")
    log.info("baseline report written to %s", report_path)
    return report


def _render(report: EvalReport, queries: list[EvalQuery],
            retrieved_by_query: dict[str, list[str]]) -> str:
    """format_report + a per-query miss detail section for analysis."""
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
        head.append(f"### {r.query_id} — {r.failure} (P={r.precision:.2f} R={r.recall:.2f})")
        head.append(f"- query: {r.query}")
        head.append(f"- retrieved: {got or '（空）'}")
        head.append(f"- relevant: {rel}")
        if missed:
            head.append(f"- 漏召: {missed}")
        head.append("")
    return "\n".join(head) + "\n"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    rep = run_v0_baseline()
    print(f"\nmean precision = {rep.mean_precision:.3f}")
    print(f"mean recall    = {rep.mean_recall:.3f}")
    print(f"failure counts = {rep.failure_counts()}")
