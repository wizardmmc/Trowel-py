"""从 active Note 派生 Dictionary 的 L0/L1 文本，不写入磁盘。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note

# 日志名属于部署过滤契约，不随包内模块路径变化。
logger = logging.getLogger("trowel_py.memory.dictionary")

_CLUSTER_SYSTEM_PROMPT = (
    "你是记忆字典生成器。把笔记按主题分组为领域。每领域给: name(kebab-case英文,如 telecom-fraud)、"
    "description(一句话讲啥)、triggers(双向触发词:领域内容词+常见查询词,逗号分隔)、"
    "note_ids(该领域笔记id列表,只从给定的id里选)。只输出 JSON,不要解释。"
)
_CLUSTER_USER_TEMPLATE = (
    "笔记列表(序号. 标题 — 摘要):\n{notes}\n\n"
    '输出 JSON: {{"domains": [{{"name":"...","description":"...","triggers":"...","note_ids":["1","2"]}}]}}\n'
    "note_ids 填上面笔记的序号(字符串)。"
)


def derive_dictionary_full(
    root: Path | str,
    provider: LLMProvider,
) -> dict[str, Any]:
    """从可解析的 active Note 派生完整 L0、L1 和领域结构。

    模型返回同名领域时，``domains`` 和 L0 保留重复项；L1 映射以领域名为键，
    因此只保留最后一个同名领域的文本。

    Args:
        root: Note 所在的 Memory 根目录。
        provider: 对 Note 进行领域聚类的模型客户端。

    Returns:
        包含 L0 文本、各领域 L1 文本和领域结构的映射。
    """
    store = MemoryStore(root)
    notes_with_id = store.load_notes_with_id({"status": "active"})
    domains = _cluster_notes(notes_with_id, provider)
    notes_by_id = {stem: note for stem, note in notes_with_id}
    l1_files = {
        domain["name"]: _render_l1(domain, notes_by_id)
        for domain in domains
    }
    return {
        "L0": _render_l0(domains),
        "L1": l1_files,
        "domains": domains,
    }


def _cluster_notes(
    notes_with_id: list[tuple[str, Note]],
    provider: LLMProvider,
) -> list[dict[str, Any]]:
    """让模型把 active Note 分组为 Dictionary 领域。

    空语料直接返回空列表，不调用 provider。非空语料按从 1 开始的序号发送给
    provider。

    Args:
        notes_with_id: Note 文件名 stem 与内容的列表。
        provider: 执行领域聚类的模型客户端。

    Returns:
        解析并补齐未归类 Note 后的领域列表。
    """
    if not notes_with_id:
        return []
    lines = [
        f"{index}. {note.title} — {note.summary}"
        for index, (_stem, note) in enumerate(notes_with_id, 1)
    ]
    user_prompt = _CLUSTER_USER_TEMPLATE.format(notes="\n".join(lines))
    response = provider.complete(_CLUSTER_SYSTEM_PROMPT, user_prompt)
    return _parse_cluster(response, notes_with_id)


def _parse_cluster(
    response: str,
    notes_with_id: list[tuple[str, Note]],
) -> list[dict[str, Any]]:
    """解析模型分组，并把未分配 Note 放入 ``misc``。

    函数贪婪截取响应中第一个 ``{`` 到最后一个 ``}`` 之间的文本。没有匹配或
    截取结果无法解码时，全部 Note 回退到 ``misc``；其他合法 JSON 的结构错误
    不会回退，可能抛出下列异常。没有解析出有效 Note 引用的领域会被丢弃，
    重复 Note 引用和重复领域名均不去重。

    Args:
        response: provider 返回的文本。
        notes_with_id: 可被分组的 Note 文件名 stem 与内容列表。

    Returns:
        清理领域名并解析 Note 引用后的领域列表。

    Raises:
        AttributeError: JSON 顶层或领域项不是对象。
        TypeError: 领域集合或 Note 引用不是可迭代值。
    """
    match = re.search(r"\{.*\}", response, re.DOTALL)
    if not match:
        logger.warning("dictionary: no JSON in cluster response")
        return _fallback_to_misc(notes_with_id)
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("dictionary: invalid cluster JSON")
        return _fallback_to_misc(notes_with_id)

    valid_stems = {stem for stem, _note in notes_with_id}
    domains: list[dict[str, Any]] = []
    for candidate in data.get("domains", []):
        name = _slugify_domain(str(candidate.get("name", "")).strip())
        if not name:
            continue
        note_ids = _resolve_note_ids(
            candidate.get("note_ids", []),
            notes_with_id,
            valid_stems,
        )
        if not note_ids:
            continue
        domains.append(
            {
                "name": name,
                "description": str(candidate.get("description", "")).strip(),
                "triggers": str(candidate.get("triggers", "")).strip(),
                "note_ids": note_ids,
            }
        )

    assigned = {
        note_id
        for domain in domains
        for note_id in domain["note_ids"]
    }
    orphans = [
        stem
        for stem, _note in notes_with_id
        if stem not in assigned
    ]
    if orphans:
        domains.append(
            {
                "name": "misc",
                "description": "未归类笔记（待全量重建时重新聚类）",
                "triggers": "",
                "note_ids": orphans,
            }
        )
    return domains


def _resolve_note_ids(
    values: list[Any],
    notes_with_id: list[tuple[str, Note]],
    valid_stems: set[str],
) -> list[str]:
    """把模型返回的序号或 stem 解析为真实 Note 文件名 stem。

    精确匹配 stem 优先；否则把值按从 1 开始的序号解释。未知 stem、越界序号
    和非整数值被忽略，重复值保留。

    Args:
        values: 模型返回的 Note 引用。
        notes_with_id: 按 prompt 序号排列的 Note 列表。
        valid_stems: 可以直接接受的 Note 文件名 stem。

    Returns:
        按输入顺序解析出的真实 stem 列表。
    """
    note_ids: list[str] = []
    for value in values:
        text = str(value)
        if text in valid_stems:
            note_ids.append(text)
            continue
        try:
            index = int(text) - 1
            if 0 <= index < len(notes_with_id):
                note_ids.append(notes_with_id[index][0])
        except (ValueError, TypeError):
            pass
    return note_ids


def _fallback_to_misc(
    notes_with_id: list[tuple[str, Note]],
) -> list[dict[str, Any]]:
    """生成一个包含全部输入 Note 的 ``misc`` 领域。

    Args:
        notes_with_id: 要放入 fallback 领域的 Note 列表。

    Returns:
        仅包含 ``misc`` 的领域列表；输入为空时仍返回一个 ``note_ids`` 为空的
        ``misc`` 领域。
    """
    return [
        {
            "name": "misc",
            "description": "LLM 聚类失败，全部归 misc 待重试",
            "triggers": "",
            "note_ids": [stem for stem, _note in notes_with_id],
        }
    ]


def _slugify_domain(name: str) -> str:
    """把模型给出的领域名清理为 L1 文件名 stem。

    空白和斜杠折叠为连字符，仅保留 ASCII 字母数字、连字符及 Unicode
    U+4E00–U+9FFF 范围字符；结果转为小写，清理后为空则返回 ``misc``。

    Args:
        name: 模型返回的领域名。

    Returns:
        可用于 L1 文件名的 stem。
    """
    slug = re.sub(r"[\s/]+", "-", name.strip())
    slug = re.sub(r"[^a-zA-Z0-9一-鿿-]", "", slug)
    return slug.lower() or "misc"


def _render_l0(domains: list[dict[str, Any]]) -> str:
    """把领域列表渲染为根索引 L0。

    每个领域的声明数量直接取 ``note_ids`` 长度；即使 L1 渲染随后跳过缺失
    Note，L0 数量也不会减少。

    Args:
        domains: 含名称、描述、触发词和 Note stem 的领域列表。

    Returns:
        以换行结尾的 L0 文本；空列表仍返回根索引说明。
    """
    lines = [
        "这是 memory 笔记的根索引。先用下面的领域列表定位该去哪个 L1，"
        "再 read 对应 L1 文件找具体笔记。",
        "",
    ]
    for domain in domains:
        lines.append(
            f"### {domain['name']}（{len(domain['note_ids'])} 条 → "
            f"read dictionary-L1/{domain['name']}.md）"
        )
        if domain["description"]:
            lines.append(domain["description"])
        if domain["triggers"]:
            lines.append(f"触发词：{domain['triggers']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_l1(
    domain: dict[str, Any],
    notes_by_id: dict[str, Note],
) -> str:
    """把一个领域及其现存 Note 渲染为 L1。

    ``note_ids`` 中不在 ``notes_by_id`` 的 stem 被跳过。

    Args:
        domain: 含名称、描述和 Note stem 的领域。
        notes_by_id: Note 文件名 stem 到内容的映射。

    Returns:
        以换行结尾的领域 L1 文本。
    """
    lines = [f"# {domain['name']}", ""]
    if domain["description"]:
        lines.extend([f"{domain['description']}。", ""])
    for stem in domain["note_ids"]:
        note = notes_by_id.get(stem)
        if note is not None:
            lines.append(_render_l1_entry(stem, note))
    return "\n".join(lines) + "\n"


def _render_l1_entry(stem: str, note: Note) -> str:
    """渲染一条可供检索和一致性检查读取的 L1 Note 条目。

    标签非空时按原顺序用 ``, `` 拼接为触发词，否则使用标题。返回文本同时
    包含旧路径格式和保存原始 stem 的 HTML anchor；一致性检查优先解析 anchor。

    Args:
        stem: Note 文件名 stem。
        note: 要渲染的 Note。

    Returns:
        单行 Markdown 列表项。
    """
    # HTML anchor 不受 Markdown code span 转义影响，供一致性检查还原原始 stem。
    triggers = ", ".join(note.tags) if note.tags else note.title
    return (
        f"- **{note.title}** → `notes/{stem}.md`：{note.summary}"
        f"｜触发词：{triggers} <!-- @stem {stem} -->"
    )
