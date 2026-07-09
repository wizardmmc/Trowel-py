"""fresh-context LLM retriever for the offline eval (slice-038 T7).

Navigates the two-layer dictionary the way S1's spike agents did: read L0 ->
pick 1-2 domain L1 files -> read them -> pick note stems. Anti-cheat (S1): the
retriever only sees the dictionary and the corpus file list, never the
ground-truth relevant set. Anything it hallucinates that is not a real file is
dropped (correctly counted as not-retrieved).

The deterministic parts (parsing L0 domain keys, parsing L1 note stems, filtering)
are unit-tested with a fake provider; the real run plugs in trowel's GLM-backed
``AnthropicProvider`` via ``baseline.py``.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.eval import Retriever

_L0_DOMAIN_RE = re.compile(r"dictionary-L1/([^\s.)]+)\.md")
_L1_STEM_RE = re.compile(r"pages/([^`]+?)\.md")
_MAX_DOMAINS = 2

_DOMAIN_SYS = (
    "你是记忆系统的检索器。只根据下面的根索引(L0)，选出与问题最相关的 1-2 个领域。"
    "只输出领域文件名(不含路径不含.md)，逗号分隔，不要解释。"
)
_NOTE_SYS = (
    "你是记忆系统的检索器。根据下面的领域索引(L1)和候选笔记文件名列表，选出与问题相关的笔记文件名。"
    "只输出文件名(不含路径不含.md)，逗号分隔，只从候选列表里选，不要解释。"
)


class LLMRetriever(Retriever):
    """A two-step fresh-context retriever over a two-layer dictionary."""

    def __init__(self, provider: LLMProvider, *, retries: int = 2) -> None:
        self._provider = provider
        self._retries = retries

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """provider.complete with light retry (GLM偶发 429/网络抖动)."""
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                return self._provider.complete(system_prompt, user_prompt)
            except Exception as exc:  # noqa: BLE001 — retry any provider error
                last_exc = exc
                time.sleep(2 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def __call__(self, query: str, *, corpus_dir: Path | str,
                 dictionary_path: Path | str) -> list[str]:
        l0_path = Path(dictionary_path)
        l0_text = l0_path.read_text(encoding="utf-8")
        l1_dir = l0_path.parent / "dictionary-L1"

        all_domains = _parse_l0_domains(l0_text)
        wanted = self._pick_domains(query, l0_text, all_domains, l1_dir)
        if not wanted:
            return []

        candidate_stems: set[str] = set()
        l1_blobs: list[str] = []
        for d in wanted:
            l1_file = l1_dir / f"{d}.md"
            if not l1_file.exists():
                continue
            text = l1_file.read_text(encoding="utf-8")
            l1_blobs.append(text)
            candidate_stems |= _parse_l1_stems(text)
        if not candidate_stems:
            return []

        picked = self._pick_notes(query, "\n\n".join(l1_blobs), candidate_stems)
        real = {p.stem for p in Path(corpus_dir).glob("*.md")}
        # drop hallucinated stems that are not real files
        ordered: list[str] = []
        for stem in picked:
            if stem in real and stem not in ordered:
                ordered.append(stem)
        return ordered

    def _pick_domains(self, query: str, l0_text: str,
                      all_domains: list[str], l1_dir: Path) -> list[str]:
        user = (
            f"问题：{query}\n\n可用领域：{', '.join(all_domains)}\n\n"
            f"根索引(L0)：\n{l0_text}"
        )
        raw = self._complete(_DOMAIN_SYS, user)
        chosen = [d.strip() for d in _split_list(raw) if d.strip()]
        # keep only valid domains (those with an L1 file), cap at 2
        valid = [d for d in chosen if d in all_domains and (l1_dir / f"{d}.md").exists()]
        return _dedupe(valid)[:_MAX_DOMAINS]

    def _pick_notes(self, query: str, l1_blob: str,
                    candidates: set[str]) -> list[str]:
        user = (
            f"问题：{query}\n\n候选笔记文件名：{', '.join(sorted(candidates))}\n\n"
            f"领域索引(L1)：\n{l1_blob}"
        )
        raw = self._complete(_NOTE_SYS, user)
        picked = [s.strip() for s in _split_list(raw) if s.strip()]
        return [s for s in picked if s in candidates]


def _parse_l0_domains(l0_text: str) -> list[str]:
    """Extract domain keys referenced as ``dictionary-L1/<key>.md`` in L0."""
    return _dedupe(_L0_DOMAIN_RE.findall(l0_text))


def _parse_l1_stems(l1_text: str) -> set[str]:
    """Extract note stems referenced as ``pages/<stem>.md`` in an L1 file."""
    return set(_L1_STEM_RE.findall(l1_text))


def _split_list(raw: str) -> list[str]:
    """Parse an LLM's comma/newline list response into trimmed items."""
    items = re.split(r"[,\n、；;]", raw)
    cleaned = []
    for it in items:
        it = it.strip().strip("`\"' ").strip()
        # the model sometimes echoes "pages/X.md" — reduce to stem
        it = re.sub(r"^pages/", "", it)
        it = re.sub(r"\.md$", "", it)
        if it:
            cleaned.append(it)
    return cleaned


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
