"""两层 dictionary 的 LLM 检索适配器，不接触评估答案。"""

from __future__ import annotations

import re
import time
from pathlib import Path

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.eval import Retriever

_L0_DOMAIN_RE = re.compile(r"dictionary-L1/([^\s.)]+)\.md")
# 兼容旧 wiki 的 `pages/` 与当前 memory 的 `notes/`。
_L1_STEM_RE = re.compile(r"(?:pages|notes)/([^`]+?)\.md")
_L1_STEM_ANCHOR_RE = re.compile(r"<!-- @stem (\S+) -->")
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
    def __init__(self, provider: LLMProvider, *, retries: int = 2) -> None:
        self._provider = provider
        self._retries = retries

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                return self._provider.complete(system_prompt, user_prompt)
            except Exception as exc:  # noqa: BLE001 - provider 失败统一重试
                last_exc = exc
                time.sleep(2 * (attempt + 1))
        assert last_exc is not None
        raise last_exc

    def __call__(
        self,
        query: str,
        *,
        corpus_dir: Path | str,
        dictionary_path: Path | str,
    ) -> list[str]:
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
        # `_pick_notes` 先过滤候选集；此处再过滤已从实际语料删除的 stem。
        ordered: list[str] = []
        for stem in picked:
            if stem in real and stem not in ordered:
                ordered.append(stem)
        return ordered

    def _pick_domains(
        self, query: str, l0_text: str, all_domains: list[str], l1_dir: Path
    ) -> list[str]:
        user = (
            f"问题：{query}\n\n可用领域：{', '.join(all_domains)}\n\n"
            f"根索引(L0)：\n{l0_text}"
        )
        raw = self._complete(_DOMAIN_SYS, user)
        chosen = [d.strip() for d in _split_list(raw) if d.strip()]
        # 只接受有 L1 文件的已知领域，并限制为两个。
        valid = [
            d for d in chosen if d in all_domains and (l1_dir / f"{d}.md").exists()
        ]
        return _dedupe(valid)[:_MAX_DOMAINS]

    def _pick_notes(self, query: str, l1_blob: str, candidates: set[str]) -> list[str]:
        user = (
            f"问题：{query}\n\n候选笔记文件名：{', '.join(sorted(candidates))}\n\n"
            f"领域索引(L1)：\n{l1_blob}"
        )
        raw = self._complete(_NOTE_SYS, user)
        picked = [s.strip() for s in _split_list(raw) if s.strip()]
        return [s for s in picked if s in candidates]


def _parse_l0_domains(l0_text: str) -> list[str]:
    return _dedupe(_L0_DOMAIN_RE.findall(l0_text))


def _parse_l1_stems(l1_text: str) -> set[str]:
    """优先读 stem anchor，避免 stem 内的反引号截断 Markdown code span。"""
    anchored = _L1_STEM_ANCHOR_RE.findall(l1_text)
    if anchored:
        return set(anchored)
    return set(_L1_STEM_RE.findall(l1_text))


def _split_list(raw: str) -> list[str]:
    items = re.split(r"[,\n、；;]", raw)
    cleaned = []
    for it in items:
        it = it.strip().strip("`\"' ").strip()
        # 兼容模型偶尔回显的 `pages/` 路径。
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
