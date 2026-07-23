"""维护 profile distill 独立于 daily review 的处理水位。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("trowel_py.memory.profile_distill_state")

_META_DIR = "meta"
_STATE_FILE = "profile-distill-state.json"


@dataclass(frozen=True)
class ProcessedSession:
    cc_session_id: str
    end_offset: int
    at: str


def _state_path(root: Path) -> Path:
    return root / _META_DIR / _STATE_FILE


def load_processed(root: Path) -> dict[str, ProcessedSession]:
    """读取独立水位；文件缺失返回空映射，JSON 损坏时显式报错。"""
    path = _state_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupt distill state at {path}: {exc}") from exc
    raw = data.get("processed", []) if isinstance(data, dict) else []
    out: dict[str, ProcessedSession] = {}
    for item in raw:
        if not isinstance(item, dict) or "cc_session_id" not in item:
            continue
        # 单条坏水位不能阻塞整个批次；跳过后该 session 会被重新提炼。
        try:
            end_offset = int(item.get("end_offset", 0))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            logger.warning(
                "distill state: corrupt end_offset %r for %s, skipping",
                item.get("end_offset"),
                item.get("cc_session_id"),
            )
            continue
        rec = ProcessedSession(
            cc_session_id=str(item["cc_session_id"]),
            end_offset=end_offset,
            at=str(item.get("at", "")),
        )
        out[rec.cc_session_id] = rec
    return out


def mark_processed(
    root: Path, cc_session_id: str, end_offset: int, *, at: str
) -> None:
    """按 session 幂等覆盖水位；调用者必须持有 distill 进程锁。"""
    existing = load_processed(root)
    existing[cc_session_id] = ProcessedSession(
        cc_session_id=cc_session_id, end_offset=end_offset, at=at
    )
    path = _state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed": [
            {
                "cc_session_id": r.cc_session_id,
                "end_offset": r.end_offset,
                "at": r.at,
            }
            for r in existing.values()
        ]
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
