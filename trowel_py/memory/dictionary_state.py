"""Dictionary 派生索引的一致性状态与最近构建结果。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

_STATE_REL = "meta/dictionary-state.json"
DictStatus = Literal["consistent", "stale", "missing"]


@dataclass(frozen=True)
class DictionaryState:
    """后续失败保留最后成功的 hash 与时间，便于判断索引曾与哪些事实一致。"""

    status: DictStatus = "missing"
    source_hash: str | None = None
    rendered_hash: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_failure_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
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
        """标记索引 stale，同时保留 last-good 水位。"""
        return replace(
            self,
            status="stale",
            last_failure_at=at,
            last_failure_reason=reason,
        )


def _opt_str(v: object) -> str | None:
    if v is None:
        return None
    return str(v)


def state_path(root: Path | str) -> Path:
    return Path(root) / _STATE_REL


def load_state(root: Path | str) -> DictionaryState:
    """缺失或损坏的状态不得使现有索引受信，统一降级为 missing。"""
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
    """原子替换状态；调用方只能在 L0/L1 发布后盖下 consistent 水位。"""
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
