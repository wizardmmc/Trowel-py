"""根据会话级证据评估笔记是否应生成 Core 候选。

候选相关写入只会创建、覆盖或阻断按 ``memory_id`` 定位的候选文件，不直接
修改 ``core.md``；证据重算可能初始化已有 sessions 数据库的 schema。候选
仍需人工批准。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

from trowel_py.memory.promotion import candidates as _candidate_io
from trowel_py.memory.promotion_policy import PromotionPolicy, default_policy
from trowel_py.memory.recompute import NoteEffect, compute_note_effects
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter
from trowel_py.memory.types import Note

_CANDIDATES_DIR = "meta/core-candidates"
_SAFE_MEMORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")

logger = logging.getLogger(__name__)


def _today_str(today: str | None) -> str:
    """选择候选元数据使用的日期文本。

    Args:
        today: 显式日期；``None`` 或空字符串时使用本机今天的 ISO 日期。

    Returns:
        未经格式校验的显式值，或本机当前日期。
    """
    return today or date.today().isoformat()


def _candidates_dir(root: Path) -> Path:
    """定位当前 Memory 根目录的 Core 候选目录。

    Args:
        root: Memory 根目录。

    Returns:
        ``<root>/meta/core-candidates``。
    """
    return _candidate_io.candidates_dir(root, _CANDIDATES_DIR)


def _hash_ids(ids: Iterable[str]) -> str:
    """生成不受输入顺序影响的会话 ID 短摘要。

    实现保留重复 ID，排序后以 ``|`` 拼接，再取 SHA-256 十六进制摘要前 16 位；
    因此它不是严格的集合摘要，ID 中的分隔符也可能造成相同拼接文本。

    Args:
        ids: 会话 ID 的可迭代对象；函数只消费一次。

    Returns:
        16 位小写十六进制摘要。
    """
    return _candidate_io.hash_ids(ids, hashlib.sha256)


def _safe_candidate_path(root: Path, memory_id: str) -> Path | None:
    """仅为受限 Memory ID 生成候选路径。

    函数要求非空 ID 通过 ``^[A-Za-z0-9_-]+$`` 的 ``re.match`` 检查；由于
    ``$`` 可匹配末尾换行前的位置，带单个末尾换行的值也会通过，但路径分隔符
    不会通过。拒绝时返回 ``None``。

    Args:
        root: Memory 根目录。
        memory_id: 要用作候选文件名的 Memory ID。

    Returns:
        ``<候选目录>/<memory_id>.md``，或 ``None``。
    """
    return _candidate_io.safe_candidate_path(
        root,
        memory_id,
        safe_pattern=_SAFE_MEMORY_ID,
        candidates_dir_fn=_candidates_dir,
    )


def _policy_hash(policy: PromotionPolicy) -> str:
    """为完整策略内容生成可追溯的短摘要。

    Args:
        policy: 要序列化的晋升策略。

    Returns:
        对键排序 JSON 计算的 SHA-256 十六进制摘要前 16 位。
    """
    return _candidate_io.policy_hash(
        policy, hash_factory=hashlib.sha256, json_module=json
    )


def _gap_flags(
    note: Note, eff: NoteEffect | None, policy: PromotionPolicy
) -> list[str]:
    """按固定顺序列出笔记未满足的晋升条件。

    没有证据对象时，三项计数均按 0 比较；本函数本身不添加
    ``no_evidence``。

    Args:
        note: 提供类型和验证状态的笔记。
        eff: 可选的会话效果统计。
        policy: 提供允许集合和计数阈值的晋升策略。

    Returns:
        依次可能包含 kind、verification、helpful_sessions、
        harmful_sessions 和 distinct_days 的缺口列表。
    """
    helpful = eff.helpful_refs if eff else 0
    harmful = eff.harmful_refs if eff else 0
    distinct = eff.distinct_days if eff else 0
    gaps: list[str] = []
    if note.kind not in policy.allowed_kinds:
        gaps.append("kind")
    if note.verification not in policy.allowed_verification:
        gaps.append("verification")
    if helpful < policy.min_helpful_sessions:
        gaps.append("helpful_sessions")
    if harmful > policy.max_harmful_sessions:
        gaps.append("harmful_sessions")
    if distinct < policy.min_distinct_days:
        gaps.append("distinct_days")
    return gaps


def _candidate_body(note: Note, eff: NoteEffect, policy: PromotionPolicy) -> str:
    """渲染供人工审批的晋升依据和原笔记正文。

    Args:
        note: 候选来源笔记。
        eff: 写入正文的会话效果统计。
        policy: 写入正文的阈值和版本。

    Returns:
        不含 frontmatter 的 Markdown 正文。
    """
    return _candidate_io.candidate_body(note, eff, policy)


def _write_candidate(
    root: Path, note: Note, eff: NoteEffect, policy: PromotionPolicy, today: str
) -> Path | None:
    """委托 candidate I/O 写入或覆盖一份晋升候选。

    调用时注入本模块的路径校验、策略与会话哈希、正文渲染和 frontmatter
    序列化函数。

    Args:
        root: Memory 根目录。
        note: 候选来源笔记。
        eff: 候选使用的会话效果统计。
        policy: 候选记录的晋升策略。
        today: 候选 ``generated_at`` 日期文本。

    Returns:
        写入路径；Memory ID 不安全时为 ``None``。

    Raises:
        OSError: 无法创建候选目录或写入文件。
    """
    return _candidate_io.write_candidate(
        root,
        note,
        eff,
        policy,
        today,
        safe_candidate_path_fn=_safe_candidate_path,
        policy_hash_fn=_policy_hash,
        hash_ids_fn=_hash_ids,
        candidate_body_fn=_candidate_body,
        dump_frontmatter_fn=_dump_frontmatter,
        logger=logger,
    )


def _mark_blocked(path: Path, policy: PromotionPolicy, today: str, reason: str) -> None:
    """委托 candidate I/O 保留正文并把既有候选标记为 blocked。

    调用时注入本模块当前的 frontmatter 拆分和序列化函数。文件没有可用
    frontmatter 时只记录告警，不写回。

    Args:
        path: 既有候选文件。
        policy: 更新候选时记录的策略版本。
        today: ``blocked_at`` 日期文本。
        reason: ``blocked_reason`` 文本。

    Raises:
        OSError: 无法读取或写入候选文件。
        UnicodeDecodeError: 候选不是有效的 UTF-8 文本。
    """
    _candidate_io.mark_blocked(
        path,
        policy,
        today,
        reason,
        split_frontmatter_fn=_split_frontmatter,
        dump_frontmatter_fn=_dump_frontmatter,
        logger=logger,
    )


def evaluate_promotion(
    root: Path | str,
    policy: PromotionPolicy | None = None,
    *,
    local_tz: Any | None = None,
    today: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """评估 active 笔记并生成、阻断或预演 Core 候选。

    只评估状态为 active 且具有非空 Memory ID 的笔记。证据按笔记 stem 关联；
    没有证据对象时只报告 ``no_evidence``，即使计数阈值为零也不允许晋升。
    有证据且全部门禁通过时，安全 ID 会加入 candidates，并在非 dry-run 模式
    覆盖同 ID 候选文件；不安全 ID 保持 ``eligible=True`` 的差距记录，但不会
    加入 candidates。

    门禁未通过时，只有有害会话数超过上限且安全候选路径存在，才加入 blocked，
    并在非 dry-run 模式保留正文、更新阻断元数据；路径若是目录，实际读取时
    会失败。dry-run 不创建或更新候选文件，但 candidates 和 blocked 仍报告
    本次本应执行的动作。本函数从不修改 ``core.md``。

    Args:
        root: Memory 根目录或路径文本，不展开 ``~``。
        policy: 晋升策略；为 ``None`` 时使用新建的默认策略。
        local_tz: 传给会话证据重算的本地时区。
        today: 候选元数据日期；``None`` 或空字符串时使用本机今天。
        dry_run: 是否只生成报告而不写候选文件。

    Returns:
        包含生效策略、候选 ID、阻断 ID、评估数量、逐笔记差距和 dry-run 标志
        的新字典；各列表保持笔记加载顺序。

    Raises:
        OSError: 无法读取 Memory 数据或读写候选文件。
        UnicodeDecodeError: 读取的 Memory 或候选文件不是有效 UTF-8 文本。
    """
    active_policy = policy or default_policy()
    root_path = Path(root)
    today_str = _today_str(today)
    effects = compute_note_effects(root_path, local_tz=local_tz)
    notes = dict(MemoryStore(root_path).load_notes_with_id())

    candidates: list[str] = []
    blocked: list[str] = []
    gaps: list[dict[str, Any]] = []
    evaluated = 0
    for stem, note in notes.items():
        if note.status != "active" or not note.memory_id:
            continue
        evaluated += 1
        eff = effects.get(stem)
        helpful = eff.helpful_refs if eff else 0
        harmful = eff.harmful_refs if eff else 0
        distinct = eff.distinct_days if eff else 0
        cand_path = _safe_candidate_path(root_path, note.memory_id)
        if eff is None:
            # 无证据对象时不进入阈值比较，零阈值也不能使其晋升。
            miss: list[str] = ["no_evidence"]
            eligible = False
        else:
            miss = _gap_flags(note, eff, active_policy)
            eligible = not miss
            if eligible:
                if cand_path is None:
                    logger.warning(
                        "unsafe memory_id %r; not promoting (C-8)", note.memory_id
                    )
                else:
                    if not dry_run:
                        _write_candidate(root_path, note, eff, active_policy, today_str)
                    candidates.append(note.memory_id)
            elif (
                harmful > active_policy.max_harmful_sessions
                and cand_path is not None
                and cand_path.exists()
            ):
                # 有害证据超限时，既有候选不能继续保持待审批状态。
                if not dry_run:
                    _mark_blocked(
                        cand_path,
                        active_policy,
                        today_str,
                        reason=(
                            f"harmful_sessions={harmful}"
                            f">{active_policy.max_harmful_sessions}"
                        ),
                    )
                blocked.append(note.memory_id)
        gaps.append(
            {
                "memory_id": note.memory_id,
                "title": note.title,
                "stem": stem,
                "kind": note.kind,
                "verification": note.verification,
                "helpful_sessions": helpful,
                "harmful_sessions": harmful,
                "distinct_days": distinct,
                "gaps": miss,
                "eligible": eligible,
            }
        )
    return {
        "policy": active_policy.to_dict(),
        "candidates": candidates,
        "blocked": blocked,
        "notes_evaluated": evaluated,
        "gaps": gaps,
        "dry_run": dry_run,
    }
