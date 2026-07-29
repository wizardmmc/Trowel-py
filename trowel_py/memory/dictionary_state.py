"""保存 Dictionary 的发布基线，以及成功发布或标记 stale 的记录。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_STATE_REL = "meta/dictionary-state.json"
# consistent 只记录最近一次成功发布，仍须结合摘要与 L0/L1 结构检查；
# stale 表示需要重建，missing 表示没有可用状态记录。
DictStatus = Literal["consistent", "stale", "missing"]


@dataclass(frozen=True)
class DictionaryState:
    """记录 Dictionary 的发布基线和当前维护状态。

    ``consistent`` 只表示状态文件记录了成功发布；完整的一致性判断还要核对
    active Note 摘要、L0/L1 渲染摘要和索引结构。状态变为 ``stale`` 时仍保留
    上次成功的摘要和时间，供检查和重建使用。

    Attributes:
        status: 当前维护状态；``consistent`` 表示记录了成功发布，但仍需结合
            摘要和索引结构检查，``stale`` 表示需要重建，``missing`` 表示没有
            可用状态记录。
        source_hash: 上次成功构建时 active Note 语料的摘要；尚无成功记录时为
            None。
        rendered_hash: 上次成功发布时 L0/L1 原文的摘要；尚无成功记录时为 None。
        last_success_at: 上次成功发布的时间文本；不在此处校验格式。
        last_failure_at: 最近一次将状态标为 ``stale`` 的时间文本；尚未标记时为
            None。
        last_failure_reason: 最近一次将状态标为 ``stale`` 的原因；尚未标记时为
            None。
    """

    status: DictStatus = "missing"
    source_hash: str | None = None
    rendered_hash: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        """返回包含全部持久化字段的 JSON 字段映射。"""
        return {
            "status": self.status,
            "source_hash": self.source_hash,
            "rendered_hash": self.rendered_hash,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_failure_reason": self.last_failure_reason,
        }

    @classmethod
    def from_dict(cls, d: object) -> "DictionaryState":
        """从宽松 JSON 值恢复 Dictionary 状态。

        非对象输入返回全默认状态。对象中的未知 ``status`` 只改为 ``missing``；
        其他非 None 字段仍转换为字符串，不校验摘要或时间格式。

        Args:
            d: JSON 解码得到的任意值。

        Returns:
            按上述容错规则恢复出的状态。
        """
        if not isinstance(d, dict):
            return cls()
        status = d.get("status")
        if status not in ("consistent", "stale", "missing"):
            status = "missing"
        return cls(
            status=status,  # type: ignore[arg-type]
            source_hash=_opt_str(d.get("source_hash")),
            rendered_hash=_opt_str(d.get("rendered_hash")),
            last_success_at=_opt_str(d.get("last_success_at")),
            last_failure_at=_opt_str(d.get("last_failure_at")),
            last_failure_reason=_opt_str(d.get("last_failure_reason")),
        )

    def with_success(
        self, source_hash: str, rendered_hash: str, at: str
    ) -> "DictionaryState":
        """返回记录本次成功发布的新状态。

        新状态写入两个摘要和成功时间，并清除此前标记 ``stale`` 的时间与原因。

        Args:
            source_hash: 本次 active Note 语料的摘要。
            rendered_hash: 本次发布的 L0/L1 原文摘要。
            at: 本次成功时间文本；不在此处校验格式。

        Returns:
            状态为 consistent 的新对象。
        """
        return replace(
            self,
            status="consistent",
            source_hash=source_hash,
            rendered_hash=rendered_hash,
            last_success_at=at,
            last_failure_at=None,
            last_failure_reason=None,
        )

    def with_failure(self, reason: str, at: str) -> "DictionaryState":
        """返回将状态标为 stale 且保留成功基线的新状态。

        Args:
            reason: 本次标记 stale 的原因。
            at: 本次标记时间文本；不在此处校验格式。

        Returns:
            保留成功摘要和成功时间的新状态。
        """
        return replace(
            self,
            status="stale",
            last_failure_at=at,
            last_failure_reason=reason,
        )


def _opt_str(v: object) -> str | None:
    """保持 None 不变，并把其他 JSON 字段值转换为字符串。"""
    if v is None:
        return None
    return str(v)


def state_path(root: Path | str) -> Path:
    """返回 Memory 根目录下的 Dictionary 状态文件路径。

    Args:
        root: Dictionary 所在的 Memory 根目录。

    Returns:
        ``meta/dictionary-state.json`` 的完整路径。
    """
    return Path(root) / _STATE_REL


def load_state(root: Path | str) -> DictionaryState:
    """读取 Dictionary 状态，缺失或损坏时降级为 missing。

    文件不存在时静默返回默认状态。读取失败或 JSON 无法解码时记录告警并返回
    默认状态；可解码值交给 ``DictionaryState.from_dict`` 宽松恢复。
    合法但非对象的 JSON 不记录告警，并返回全默认状态。

    Args:
        root: Dictionary 所在的 Memory 根目录。

    Returns:
        已保存的状态，或默认 missing 状态。
    """
    path = state_path(root)
    if not path.exists():
        return DictionaryState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[memory] dictionary state corrupt (%s) — treating as missing", exc
        )
        return DictionaryState()
    return DictionaryState.from_dict(data)


def save_state(root: Path | str, state: DictionaryState) -> None:
    """通过同目录临时文件原子替换 Dictionary 状态。

    函数本身不加锁，并复用固定的 ``.tmp`` 路径，调用方须避免并发写入。
    ``consistent`` 状态只能在对应 L0/L1 发布成功后保存。替换失败时旧状态保持
    不变，但临时文件可能保留。

    Args:
        root: Dictionary 所在的 Memory 根目录。
        state: 要完整写入的状态。

    Raises:
        OSError: 无法创建目录、写入临时文件或替换状态文件。
    """
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
