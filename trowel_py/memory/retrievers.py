"""用 LLM 导航两层 dictionary；检索提示不注入评估标注答案。"""

from __future__ import annotations

import re
import time
from pathlib import Path

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.eval import Retriever

_L0_DOMAIN_RE = re.compile(r"dictionary-L1/([^\s.)]+)\.md")
# L1 路径解析同时兼容旧 wiki 的 pages/ 和当前 memory 的 notes/。
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
    """先选择 L0 领域，再用 L1 候选和 corpus 顶层路径过滤 note stem。"""

    def __init__(self, provider: LLMProvider, *, retries: int = 2) -> None:
        """保存 provider 和每次模型选择失败后的重试次数。

        ``retries`` 不做校验；非负值表示总尝试次数为 ``retries + 1``。
        """
        self._provider = provider
        self._retries = retries

    def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用模型，第 n 次 provider 失败后等待 ``2n`` 秒。

        ``sleep`` 位于捕获范围外，其异常会立即传播；等待成功时，即使已是最后
        一次尝试也会等待，再重新抛出 provider 异常。``BaseException`` 不捕获；
        retries 为负数时不会调用 provider，而会触发末尾断言。
        """
        last_exc: Exception | None = None
        for attempt in range(self._retries + 1):
            try:
                return self._provider.complete(system_prompt, user_prompt)
            except Exception as exc:  # noqa: BLE001 - 统一等待后重试或重抛
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
        """通过 L0 和 L1 选择查询相关的现存 note stem。

        L1 目录固定取根索引同级的 ``dictionary-L1``。领域必须同时出现在 L0
        且有对应路径；按模型顺序过滤并去重后最多取前两个，重复项不占名额。
        L1 路径只检查 ``exists()``，不要求是普通文件。note 必须出现在所选 L1
        候选中，并在 ``corpus_dir`` 顶层仍有同 stem 的 ``*.md`` 匹配路径。
        返回值保留模型选择顺序并去重；corpus 匹配路径也不要求是普通文件。

        Args:
            query: 交给两阶段选择器的自然语言问题。
            corpus_dir: 用于最终存在性过滤的 note 目录。
            dictionary_path: L0 根索引文件。

        Returns:
            通过 L1 候选和 corpus 顶层匹配路径双重过滤的 note stem。

        Raises:
            UnicodeError: L0 或被选择的 L1 文件不是有效 UTF-8。
            OSError: L0、L1 读取或未被忽略的路径检查失败。
            Exception: provider 尝试耗尽后重抛的异常，或等待期间产生的异常。
        """
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
        # L1 候选过滤后，再要求 corpus 顶层存在同 stem 的 *.md 匹配路径。
        ordered: list[str] = []
        for stem in picked:
            if stem in real and stem not in ordered:
                ordered.append(stem)
        return ordered

    def _pick_domains(
        self, query: str, l0_text: str, all_domains: list[str], l1_dir: Path
    ) -> list[str]:
        """过滤未知或无路径领域，按首次出现去重后返回前两个。"""
        user = (
            f"问题：{query}\n\n可用领域：{', '.join(all_domains)}\n\n"
            f"根索引(L0)：\n{l0_text}"
        )
        raw = self._complete(_DOMAIN_SYS, user)
        chosen = [d.strip() for d in _split_list(raw) if d.strip()]
        # 模型输出必须同时通过 L0 白名单和 L1 路径存在性检查。
        valid = [
            d for d in chosen if d in all_domains and (l1_dir / f"{d}.md").exists()
        ]
        return _dedupe(valid)[:_MAX_DOMAINS]

    def _pick_notes(self, query: str, l1_blob: str, candidates: set[str]) -> list[str]:
        """保留模型返回顺序，仅过滤掉不在 L1 候选集合中的 stem。"""
        user = (
            f"问题：{query}\n\n候选笔记文件名：{', '.join(sorted(candidates))}\n\n"
            f"领域索引(L1)：\n{l1_blob}"
        )
        raw = self._complete(_NOTE_SYS, user)
        picked = [s.strip() for s in _split_list(raw) if s.strip()]
        return [s for s in picked if s in candidates]


def _parse_l0_domains(l0_text: str) -> list[str]:
    """按出现顺序提取 ``dictionary-L1/<domain>.md`` 并去重。"""
    return _dedupe(_L0_DOMAIN_RE.findall(l0_text))


def _parse_l1_stems(l1_text: str) -> set[str]:
    """提取 L1 note stem；只要存在 anchor，就完全忽略路径形式的结果。"""
    anchored = _L1_STEM_ANCHOR_RE.findall(l1_text)
    if anchored:
        return set(anchored)
    return set(_L1_STEM_RE.findall(l1_text))


def _split_list(raw: str) -> list[str]:
    """按英文逗号、顿号、中英文分号或换行拆分并清理模型输出。

    每项移除首尾空白、引号和反引号，再去掉旧 ``pages/`` 前缀及末尾
    ``.md``；不处理 ``notes/`` 前缀、项目符号或编号。
    """
    items = re.split(r"[,\n、；;]", raw)
    cleaned = []
    for it in items:
        it = it.strip().strip("`\"' ").strip()
        # 仅兼容模型偶尔回显的旧 pages/ 路径。
        it = re.sub(r"^pages/", "", it)
        it = re.sub(r"\.md$", "", it)
        if it:
            cleaned.append(it)
    return cleaned


def _dedupe(seq: list[str]) -> list[str]:
    """按首次出现顺序去重字符串列表。"""
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
