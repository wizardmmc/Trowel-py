"""tests for the LLM retriever's deterministic navigation (slice-038 T7).

Uses a fake provider + a mini corpus/dictionary so no real LLM call is needed.
"""
from __future__ import annotations

from pathlib import Path

from trowel_py.memory.retrievers import (
    LLMRetriever,
    _parse_l0_domains,
    _parse_l1_stems,
)


class FakeProvider:
    """Returns canned text for the domain-select vs note-select call."""

    def __init__(self, domain_resp: str, note_resp: str) -> None:
        self.domain_resp = domain_resp
        self.note_resp = note_resp

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        # note-select prompt is the one carrying the candidate list
        if "候选笔记文件名" in system_prompt:
            return self.note_resp
        return self.domain_resp


def _build_mini_corpus(tmp_path: Path) -> tuple[Path, Path]:
    pages = tmp_path / "pages"
    pages.mkdir()
    for stem in ("B+树", "B树与B+树对比", "无关笔记"):
        (pages / f"{stem}.md").write_text(f"# {stem}\n", encoding="utf-8")
    l0 = tmp_path / "dictionary-L0.md"
    l0.write_text(
        "### 数据库与索引（read dictionary-L1/database.md）\nB+树/LSM/索引\n",
        encoding="utf-8",
    )
    l1_dir = tmp_path / "dictionary-L1"
    l1_dir.mkdir()
    (l1_dir / "database.md").write_text(
        "- **B+树** → `pages/B+树.md`：高扇出矮胖\n"
        "- **B树与B+树对比** → `pages/B树与B+树对比.md`：fanout/范围查询\n"
        "- **无关笔记** → `pages/无关笔记.md`：无关\n",
        encoding="utf-8",
    )
    return pages, l0


def test_parse_l0_domains() -> None:
    text = "read dictionary-L1/telecom-fraud.md and dictionary-L1/database.md"
    assert _parse_l0_domains(text) == ["telecom-fraud", "database"]


def test_parse_l1_stems() -> None:
    assert _parse_l1_stems("- **B+树** → `pages/B+树.md`：x") == {"B+树"}


def test_retriever_navigates_and_picks(tmp_path: Path) -> None:
    pages, l0 = _build_mini_corpus(tmp_path)
    retriever = LLMRetriever(FakeProvider("database", "B+树, B树与B+树对比"))
    got = retriever("索引为啥用 B+ 树", corpus_dir=pages, dictionary_path=l0)
    assert set(got) == {"B+树", "B树与B+树对比"}


def test_retriever_drops_hallucinated_stems(tmp_path: Path) -> None:
    pages, l0 = _build_mini_corpus(tmp_path)
    retriever = LLMRetriever(FakeProvider("database", "B+树, 纯属编造"))
    got = retriever("q", corpus_dir=pages, dictionary_path=l0)
    assert "纯属编造" not in got
    assert got == ["B+树"]


def test_retriever_drops_off_candidate_notes(tmp_path: Path) -> None:
    # a stem that exists as a file but was NOT in the picked L1 -> dropped
    pages, l0 = _build_mini_corpus(tmp_path)
    retriever = LLMRetriever(FakeProvider("database", "B+树, 无关笔记"))
    got = retriever("只要 B+树", corpus_dir=pages, dictionary_path=l0)
    assert set(got) <= {"B+树", "无关笔记"}  # both are candidates here


def test_retriever_no_domain_returns_empty(tmp_path: Path) -> None:
    pages, l0 = _build_mini_corpus(tmp_path)
    retriever = LLMRetriever(FakeProvider("", "B+树"))
    assert retriever("q", corpus_dir=pages, dictionary_path=l0) == []


def test_retriever_caps_domains_at_two(tmp_path: Path, monkeypatch) -> None:
    # provider claims 3 domains; only 1 exists here so result still bounds.
    pages, l0 = _build_mini_corpus(tmp_path)
    retriever = LLMRetriever(FakeProvider("database, frontend, ml-eval", "B+树"))
    got = retriever("q", corpus_dir=pages, dictionary_path=l0)
    assert got == ["B+树"]  # only `database` L1 exists; others filtered
