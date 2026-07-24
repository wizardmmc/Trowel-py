"""画像建议队列的稳定入口。"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Sequence, cast as cast

try:
    import fcntl
except ImportError:  # pragma: no cover - 非 Unix 平台没有 flock
    fcntl = None  # type: ignore[assignment]

from trowel_py.memory.profile_suggestions.codec import (
    suggestion_from_dict as _decode_suggestion,
)
from trowel_py.memory.profile_suggestions.codec import (
    suggestion_to_dict as _encode_suggestion,
)
from trowel_py.memory.profile_suggestions.repository import (
    load_queue as _load_queue_file,
)
from trowel_py.memory.profile_suggestions.repository import (
    queue_path as _repository_queue_path,
)
from trowel_py.memory.profile_suggestions.repository import (
    suggestions_lock as _repository_lock,
)
from trowel_py.memory.profile_suggestions.repository import (
    write_queue as _write_queue_file,
)
from trowel_py.memory.types import (
    ProfileDimension as ProfileDimension,
)
from trowel_py.memory.types import Suggestion, SuggestionStatus

_META_DIR = "meta"
_SUGGESTIONS_FILE = "profile-suggestions.json"
logger = logging.getLogger(__name__)

# 旧记录保留自身版本，新策略只影响默认展示范围。
PROFILE_DISTILL_POLICY_VERSION = 2

_VALID_DIMS: frozenset[str] = frozenset(
    {"ability", "methodology", "expression", "goal", "other"}
)
_VALID_STATUSES: frozenset[str] = frozenset({"pending", "accepted", "discarded"})


@contextlib.contextmanager
def _suggestions_lock(root: Path):
    """锁住队列的完整读改写周期。"""
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
    """返回 memory root 下的建议队列路径。"""
    return _repository_queue_path(
        root,
        meta_dir=_META_DIR,
        filename=_SUGGESTIONS_FILE,
    )


def _suggestion_from_dict(item: dict[str, object]) -> Suggestion:
    """解析并校验一条磁盘记录。"""
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
    """把建议编码为稳定的磁盘字段。"""
    return _encode_suggestion(s)


def _load_queue(root: Path) -> tuple[list[Suggestion], str]:
    """加载建议及最近一次追加时间；文件不存在时返回空值。"""
    return _load_queue_file(
        _queue_path(root),
        decode=_suggestion_from_dict,
        loads=json.loads,
        decode_error=json.JSONDecodeError,
    )


def _write_queue(root: Path, items: Sequence[Suggestion], *, updated: str) -> None:
    """覆盖写入完整队列。"""
    _write_queue_file(
        _queue_path(root),
        items,
        updated=updated,
        encode=suggestion_to_dict,
        dumps=json.dumps,
    )


def load_suggestions(root: Path) -> list[Suggestion]:
    """返回队列中的全部建议。"""
    items, _updated = _load_queue(root)
    return items


def append_suggestions(
    root: Path, items: Sequence[Suggestion], *, updated: str
) -> None:
    """追加建议并更新队列时间戳。"""
    with _suggestions_lock(root):
        existing, _old_updated = _load_queue(root)
        _write_queue(root, [*existing, *items], updated=updated)


def update_suggestion_status(
    root: Path, suggestion_id: str, status: SuggestionStatus
) -> None:
    """更新一条建议的状态，并保留顺序与追加时间戳。"""
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
    """返回指定策略版本中尚未处理的建议。"""
    items = [item for item in load_suggestions(root) if item.status == "pending"]
    if current_policy_version is None:
        return items
    return [item for item in items if item.policy_version == current_policy_version]
