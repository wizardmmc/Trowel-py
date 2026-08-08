"""从缺少 turn ID 的 Claude Code 历史中定位一轮事件。"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any


def select_cc_turn_by_input_hash(
    events: Sequence[Any],
    input_hash: str,
    *,
    occurrence: int | None = None,
) -> list[Any]:
    """按用户输入 SHA-256 选中一个 user 边界到下个 user 边界。

    Args:
        events: Claude Code JSONL 解析得到的 TrowelEvent 序列。
        input_hash: discussion attempt 保存的精确公共输入哈希。
        occurrence: 同一输入从 1 开始的目标出现次序；省略时只接受唯一命中。

    Returns:
        精确命中的单轮事件；无法无歧义定位时返回空列表。
    """

    starts = [
        index
        for index, event in enumerate(events)
        if getattr(event, "type", None) == "user"
        and isinstance(getattr(event, "text", None), str)
        and hashlib.sha256(event.text.encode("utf-8")).hexdigest() == input_hash
    ]
    if occurrence is None:
        if len(starts) != 1:
            return []
        selected_index = 0
    elif occurrence < 1 or occurrence > len(starts):
        return []
    else:
        selected_index = occurrence - 1
    start = starts[selected_index]
    end = next(
        (
            index
            for index in range(start + 1, len(events))
            if getattr(events[index], "type", None) == "user"
        ),
        len(events),
    )
    return list(events[start:end])
