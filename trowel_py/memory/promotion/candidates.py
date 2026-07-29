"""组装晋升候选路径、摘要、Markdown 正文和 frontmatter 持久化内容。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from trowel_py.memory.promotion_policy import PromotionPolicy
from trowel_py.memory.recompute import NoteEffect
from trowel_py.memory.types import Note


def candidates_dir(root: Path, directory: str) -> Path:
    """按 ``Path`` 拼接规则组装候选目录。

    Args:
        root: Memory 根目录。
        directory: 相对目录名或绝对路径；绝对路径会使 ``root`` 被忽略。

    Returns:
        ``root / directory`` 的结果。
    """
    return root / directory


def hash_ids(ids: Iterable[str], hash_factory: Any) -> str:
    """对排序并拼接后的 ID 文本生成短摘要。

    重复 ID 会保留，``|`` 用作无转义分隔符，因此不同输入可能产生相同拼接
    文本。哈希实现由调用方注入，其异常原样传播。

    Args:
        ids: 会话 ID 的可迭代对象；函数只消费一次。
        hash_factory: 接收 UTF-8 字节并返回带 ``hexdigest`` 方法对象的工厂。

    Returns:
        完整十六进制摘要的前 16 个字符。
    """
    joined = "|".join(sorted(ids))
    return hash_factory(joined.encode("utf-8")).hexdigest()[:16]


def safe_candidate_path(
    root: Path,
    memory_id: str,
    *,
    safe_pattern: Any,
    candidates_dir_fn: Callable[[Path], Path],
) -> Path | None:
    """为通过调用方模式检查的 Memory ID 生成候选路径。

    函数仅检查 ID 非空且 ``safe_pattern.match(memory_id)`` 为真，不要求匹配
    覆盖整个字符串；路径安全性取决于注入模式和目录函数。

    Args:
        root: Memory 根目录。
        memory_id: 要用作文件名主体的 Memory ID。
        safe_pattern: 提供 ``match`` 方法的校验对象。
        candidates_dir_fn: 根据根目录返回候选目录的函数。

    Returns:
        ``<候选目录>/<memory_id>.md``；ID 为空或模式不匹配时为 ``None``。
    """
    if not memory_id or not safe_pattern.match(memory_id):
        return None
    return candidates_dir_fn(root) / f"{memory_id}.md"


def policy_hash(policy: PromotionPolicy, *, hash_factory: Any, json_module: Any) -> str:
    """序列化完整策略并生成短摘要。

    Args:
        policy: 要通过 ``to_dict`` 转换的晋升策略。
        hash_factory: 接收 UTF-8 字节并返回带 ``hexdigest`` 方法对象的工厂。
        json_module: 提供 ``dumps`` 的 JSON 兼容对象。

    Returns:
        对键排序 JSON 文本所得十六进制摘要的前 16 个字符。
    """
    return hash_factory(
        json_module.dumps(policy.to_dict(), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def candidate_body(note: Note, eff: NoteEffect, policy: PromotionPolicy) -> str:
    """渲染供人工审批的晋升依据和原笔记正文。

    摘要或正文为假值时写入空段落；有害会话数为真时写出数量和上限，否则写
    “没有发现有害反例”。结果不含 frontmatter；正文原样追加，函数不主动
    裁剪或补齐其末尾换行。

    Args:
        note: 提供标题、摘要、正文、类型和验证状态的来源笔记。
        eff: 提供帮助、有害和跨日证据计数的统计。
        policy: 提供阈值和策略版本。

    Returns:
        固定章节顺序的 Markdown 正文。
    """
    lines = [
        f"# 候选：{note.title}",
        "",
        note.summary or "",
        "",
        "## 晋升依据",
        "",
        f"- 在 {eff.helpful_refs} 个独立用户会话中被判为有帮助"
        f"（策略下限 {policy.min_helpful_sessions}）。",
        f"- 证据跨 {eff.distinct_days} 个不同日期"
        f"（策略下限 {policy.min_distinct_days}）。",
    ]
    if eff.harmful_refs:
        lines.append(
            f"- 存在 {eff.harmful_refs} 个有害反例会话"
            f"（策略上限 {policy.max_harmful_sessions}）。"
        )
    else:
        lines.append("- 没有发现有害反例。")
    lines += [
        f"- 笔记类型 {note.kind}，验证状态 {note.verification}。",
        f"- 依据策略版本 {policy.version}生成；仅供人工 review，"
        "确认后经 approve 移入 core.md。",
        "",
        "## 正文",
        "",
        note.body or "",
    ]
    return "\n".join(lines)


def write_candidate(
    root: Path,
    note: Note,
    eff: NoteEffect,
    policy: PromotionPolicy,
    today: str,
    *,
    safe_candidate_path_fn: Callable[[Path, str], Path | None],
    policy_hash_fn: Callable[[PromotionPolicy], str],
    hash_ids_fn: Callable[[Iterable[str]], str],
    candidate_body_fn: Callable[[Note, NoteEffect, PromotionPolicy], str],
    dump_frontmatter_fn: Callable[[dict[str, Any], str], str],
    logger: Any,
) -> Path | None:
    """把晋升证据和笔记正文覆盖写成候选文件。

    路径回调返回 ``None`` 时只记录告警。其他路径会创建父目录，并在
    frontmatter 中写入 memory_id、来源标题、类型、验证状态、帮助会话数、
    有害会话数、跨日数、会话 ID 摘要、策略版本与摘要、生成日期和
    ``candidate`` 状态。函数信任注入路径，不再次检查其位置；写入不使用
    临时文件原子替换。所有注入回调异常原样传播。

    Args:
        root: Memory 根目录。
        note: 候选来源笔记。
        eff: 候选记录的会话效果统计。
        policy: 候选记录的晋升策略。
        today: ``generated_at`` 日期文本。
        safe_candidate_path_fn: 校验 ID 并返回目标路径的函数。
        policy_hash_fn: 生成策略摘要的函数。
        hash_ids_fn: 生成会话 ID 摘要的函数。
        candidate_body_fn: 渲染候选正文的函数。
        dump_frontmatter_fn: 合并 frontmatter 和正文的函数。
        logger: 提供 ``warning`` 的日志对象。

    Returns:
        已写入的目标路径；路径回调拒绝 ID 时为 ``None``。

    Raises:
        OSError: 无法创建父目录或写入候选文件。
    """
    path = safe_candidate_path_fn(root, note.memory_id)
    if path is None:
        logger.warning(
            "unsafe memory_id %r; skipping candidate (C-8 path-escape guard)",
            note.memory_id,
        )
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fm: dict[str, Any] = {
        "type": "core-candidate",
        "memory_id": note.memory_id,
        "source_title": note.title,
        "kind": note.kind,
        "verification": note.verification,
        "policy_version": policy.version,
        "policy_hash": policy_hash_fn(policy),
        "helpful_sessions": eff.helpful_refs,
        "harmful_sessions": eff.harmful_refs,
        "distinct_days": eff.distinct_days,
        "helpful_session_ids_hash": hash_ids_fn(eff.helpful_sessions),
        "harmful_session_ids_hash": hash_ids_fn(eff.harmful_sessions),
        "generated_at": today,
        "status": "candidate",
    }
    path.write_text(
        dump_frontmatter_fn(fm, candidate_body_fn(note, eff, policy)), encoding="utf-8"
    )
    return path


def mark_blocked(
    path: Path,
    policy: PromotionPolicy,
    today: str,
    reason: str,
    *,
    split_frontmatter_fn: Callable[[str], tuple[dict[str, Any] | None, str]],
    dump_frontmatter_fn: Callable[[dict[str, Any], str], str],
    logger: Any,
) -> None:
    """保留解析后的正文并把候选 frontmatter 改为 blocked。

    frontmatter 解析结果为 ``None`` 时只记录告警，不写文件。否则复制原字段，
    覆盖状态、原因、策略版本和阻断日期，再直接覆盖原路径；写入不是原子替换。
    注入的拆分、序列化和日志依赖异常原样传播。

    Args:
        path: 要读取并覆盖的候选路径。
        policy: 提供新 ``policy_version`` 的策略。
        today: 新 ``blocked_at`` 日期文本。
        reason: 新 ``blocked_reason`` 文本。
        split_frontmatter_fn: 拆分原 frontmatter 和正文的函数。
        dump_frontmatter_fn: 合并新 frontmatter 和原正文的函数。
        logger: 提供 ``warning`` 的日志对象。

    Raises:
        OSError: 无法读取或写入候选路径。
        UnicodeDecodeError: 候选不是有效的 UTF-8 文本。
    """
    fm, body = split_frontmatter_fn(path.read_text(encoding="utf-8"))
    if fm is None:
        logger.warning("candidate %s has no frontmatter; not marking blocked", path)
        return
    new_fm: dict[str, Any] = dict(fm)
    new_fm["status"] = "blocked"
    new_fm["blocked_reason"] = reason
    new_fm["policy_version"] = policy.version
    new_fm["blocked_at"] = today
    path.write_text(dump_frontmatter_fn(new_fm, body), encoding="utf-8")
