"""提供画像建议队列的统一读写、状态更新和筛选入口。

公开函数定义在本模块；JSON 编解码、路径和锁操作在调用时使用本模块传入的
存储常量与依赖。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Sequence, cast as cast

try:
    import fcntl
except ImportError:  # pragma: no cover - 没有 flock 的平台会退化为无跨进程锁
    fcntl = None  # type: ignore[assignment]

from trowel_py.profile.suggestions.codec import (
    suggestion_from_dict as _decode_suggestion,
)
from trowel_py.profile.suggestions.codec import (
    suggestion_to_dict as _encode_suggestion,
)
from trowel_py.profile.suggestions.repository import (
    load_queue as _load_queue_file,
)
from trowel_py.profile.suggestions.repository import (
    queue_path as _repository_queue_path,
)
from trowel_py.profile.suggestions.repository import (
    suggestions_lock as _repository_lock,
)
from trowel_py.profile.suggestions.repository import (
    write_queue as _write_queue_file,
)
from trowel_py.profile.models import (
    ProfileDimension as ProfileDimension,
)
from trowel_py.profile.models import Suggestion, SuggestionStatus

_META_DIR = "meta"
_SUGGESTIONS_FILE = "profile-suggestions.json"
logger = logging.getLogger(__name__)

# 旧记录保留原策略版本；默认待处理查询只返回当前版本。
PROFILE_DISTILL_POLICY_VERSION = 2

_VALID_DIMS: frozenset[str] = frozenset(
    {"ability", "methodology", "expression", "goal", "other"}
)
_VALID_STATUSES: frozenset[str] = frozenset({"pending", "accepted", "discarded"})


@contextlib.contextmanager
def _suggestions_lock(root: Path) -> Iterator[None]:
    """在支持 ``flock`` 的平台独占建议队列读改写周期。

    锁文件是 ``<root>/meta/.suggestions.lock``。不支持 ``fcntl`` 时直接进入
    上下文，不创建目录，也不提供跨进程互斥。

    Args:
        root: Memory 根目录。

    Yields:
        调用方的执行区间；支持 ``flock`` 时，进入该区间前已获得独占锁。

    Raises:
        OSError: 无法创建锁目录、打开锁文件、加解锁或关闭描述符。
    """
    with _repository_lock(
        root,
        meta_dir=_META_DIR,
        file_lock=fcntl,
        open_file=os.open,
        close_file=os.close,
        create_flag=os.O_CREAT,
        read_write_flag=os.O_RDWR,
    ):
        yield


def _queue_path(root: Path) -> Path:
    """定位本模块使用的建议队列文件。

    Args:
        root: Memory 根目录。

    Returns:
        由本模块当前存储常量组装的队列路径。
    """
    return _repository_queue_path(
        root,
        meta_dir=_META_DIR,
        filename=_SUGGESTIONS_FILE,
    )


def _suggestion_from_dict(item: dict[str, object]) -> Suggestion:
    """把一条磁盘记录解码为建议，并校验 ID、维度和状态。

    ID 缺失或为假值时拒绝记录。状态缺失时取 ``pending``，策略版本为布尔
    值、缺失或无法转为整数时取 1。sources 仅在原值为列表时逐项转为字符串，
    其他类型按空元组处理；body 的假值变为空字符串，date 仅在缺失时取空
    字符串，随后两者都转为字符串。

    Args:
        item: 一条 JSON 对象记录。

    Returns:
        兼容旧版本字段的建议对象。

    Raises:
        ValueError: ID 缺失或为假值，或维度、状态不在允许集合中。
        TypeError: 维度或状态是列表、字典等不可哈希值。
    """
    return _decode_suggestion(
        item,
        valid_dimensions=_VALID_DIMS,
        valid_statuses=_VALID_STATUSES,
        suggestion_type=Suggestion,
        cast_value=cast,
        dimension_type=ProfileDimension,
        status_type=SuggestionStatus,
    )


def suggestion_to_dict(s: Suggestion) -> dict[str, object]:
    """按稳定字段顺序把建议编码为新字典。

    Args:
        s: 要编码的建议。

    Returns:
        包含 ID、维度、正文、来源、日期、状态和策略版本的字典；来源转换为新
        列表。
    """
    return _encode_suggestion(s)


def _load_queue(root: Path) -> tuple[list[Suggestion], str]:
    """读取并解码完整队列及其更新时间。

    文件缺失或 JSON 顶层不是对象时返回空值；suggestions 中非对象条目静默
    跳过，对象条目按 ``_suggestion_from_dict`` 校验。函数不获取队列锁。

    Args:
        root: Memory 根目录。

    Returns:
        按磁盘顺序排列的建议新列表和字符串化的 ``updated`` 值。

    Raises:
        OSError: 无法读取队列文件。
        UnicodeDecodeError: 队列不是有效的 UTF-8 文本。
        ValueError: 队列不是合法 JSON，或对象条目未通过校验。
        TypeError: ``suggestions`` 不可迭代，或记录的维度、状态不可哈希。
    """
    return _load_queue_file(
        _queue_path(root),
        decode=_suggestion_from_dict,
        loads=json.loads,
        decode_error=json.JSONDecodeError,
    )


def _write_queue(root: Path, items: Sequence[Suggestion], *, updated: str) -> None:
    """以稳定 JSON 格式覆盖写入完整建议队列。

    写入会创建父目录，使用两空格缩进、保留非 ASCII 字符且不追加换行。操作
    不使用临时文件替换，调用方负责在需要时持有队列锁。

    Args:
        root: Memory 根目录。
        items: 按目标磁盘顺序写入的建议。
        updated: 原样写入顶层 ``updated`` 字段的时间文本。

    Raises:
        OSError: 无法创建目录或写入队列文件。
        TypeError: 建议字段无法序列化为 JSON。
    """
    _write_queue_file(
        _queue_path(root),
        items,
        updated=updated,
        encode=suggestion_to_dict,
        dumps=json.dumps,
    )


def load_suggestions(root: Path) -> list[Suggestion]:
    """无锁读取队列中的全部建议。

    并发写入采用非原子覆盖，因此读取可能看到尚未写完的内容，并因 UTF-8 或
    JSON 解析失败而抛出异常。

    Args:
        root: Memory 根目录。

    Returns:
        按磁盘顺序排列的建议新列表；文件缺失时为空。

    Raises:
        OSError: 无法读取队列文件。
        UnicodeDecodeError: 队列不是有效的 UTF-8 文本。
        ValueError: 队列不是合法 JSON，或对象条目未通过校验。
        TypeError: 队列结构或记录字段类型无法处理。
    """
    items, _updated = _load_queue(root)
    return items


def append_suggestions(
    root: Path, items: Sequence[Suggestion], *, updated: str
) -> None:
    """在队列末尾追加建议并覆盖更新时间。

    支持 ``flock`` 时，读取、拼接和覆盖写入位于同一独占锁内；其他平台无
    跨进程互斥。函数不按 ID 去重，空输入也会创建或重写队列并更新
    ``updated``。文件覆盖不是原子替换。

    Args:
        root: Memory 根目录。
        items: 按给定顺序追加的建议。
        updated: 本次写入使用的顶层更新时间文本。

    Raises:
        OSError: 锁操作、读取、创建目录或写入失败。
        ValueError: 现有队列无法解析或含非法记录。
        TypeError: 现有队列结构或待写字段类型无法处理。
    """
    with _suggestions_lock(root):
        existing, _old_updated = _load_queue(root)
        _write_queue(root, [*existing, *items], updated=updated)


def append_suggestions_once(
    root: Path, items: Sequence[Suggestion], *, updated: str
) -> None:
    """按建议 ID 幂等追加批处理结果。

    已存在 ID 保留原记录和状态；本次输入中重复 ID 只追加第一次。该入口供
    Profile 自动提炼使用，使“队列已写、来源水位未写”的故障重试不会重复
    产生建议。手工队列兼容入口 ``append_suggestions`` 仍保留原有允许重复语义。

    Args:
        root: Memory 根目录。
        items: 使用稳定 ID 的批处理建议。
        updated: 本次写入的更新时间。
    """

    with _suggestions_lock(root):
        existing, old_updated = _load_queue(root)
        seen = {item.id for item in existing}
        appended: list[Suggestion] = []
        for item in items:
            if item.id in seen:
                continue
            seen.add(item.id)
            appended.append(item)
        if not appended:
            return
        _write_queue(
            root,
            [*existing, *appended],
            updated=updated or old_updated,
        )


def update_suggestion_status(
    root: Path, suggestion_id: str, status: SuggestionStatus
) -> None:
    """更新所有同 ID 建议的状态，并保留顺序和更新时间。

    状态在加锁前校验；非法状态或队列中没有目标 ID 时不改写建议队列文件。
    支持 ``flock`` 时，读取与非原子覆盖写入位于同一独占锁内。

    Args:
        root: Memory 根目录。
        suggestion_id: 要匹配的建议 ID；重复 ID 会全部更新。
        status: ``pending``、``accepted`` 或 ``discarded``。

    Raises:
        ValueError: 状态非法，或现有队列无法解析。
        KeyError: 队列中没有目标 ID。
        OSError: 锁操作、读取、创建目录或写入失败。
        TypeError: 现有队列结构或写入字段类型无法处理。
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status {status!r}")
    with _suggestions_lock(root):
        items, updated = _load_queue(root)
        found = False
        new_items: list[Suggestion] = []
        for suggestion in items:
            if suggestion.id == suggestion_id:
                new_items.append(replace(suggestion, status=status))
                found = True
            else:
                new_items.append(suggestion)
        if not found:
            raise KeyError(suggestion_id)
        _write_queue(root, new_items, updated=updated)


def pending_suggestions(
    root: Path, *, current_policy_version: int = PROFILE_DISTILL_POLICY_VERSION
) -> list[Suggestion]:
    """按磁盘顺序返回指定策略版本的待处理建议。

    先筛选 ``status == "pending"``，再按策略版本做精确相等比较。调用方即使
    传入类型标注之外的 ``None``，也可关闭版本过滤并返回所有版本；函数只读，
    不会升级或重写旧记录。

    Args:
        root: Memory 根目录。
        current_policy_version: 目标策略版本；运行时传入 ``None`` 表示不过滤。

    Returns:
        满足状态和版本条件的建议新列表。

    Raises:
        OSError: 无法读取队列文件。
        UnicodeDecodeError: 队列不是有效的 UTF-8 文本。
        ValueError: 队列不是合法 JSON，或对象条目未通过校验。
        TypeError: 队列结构或记录字段类型无法处理。
    """
    items = [item for item in load_suggestions(root) if item.status == "pending"]
    if current_policy_version is None:
        return items
    return [item for item in items if item.policy_version == current_policy_version]
