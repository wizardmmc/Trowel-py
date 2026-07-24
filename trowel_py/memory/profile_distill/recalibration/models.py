"""画像重校准的数据契约与共享常量。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from trowel_py.memory.sessions_repo import SessionRecord
from trowel_py.memory.types import Suggestion

ReplayHostFactory = Callable[[SessionRecord, Path], Any]

_META_DIR = "meta"
_RECALIBRATION_DIR = "profile-recalibration"
_BASELINE_DIR = "baseline"
_WORK_DIR = "work"
_MANIFEST_FILE = "manifest.json"
_STAGED_FILE = "staged-suggestions.json"
_REPORT_FILE = "report.json"

_LIVE_PROFILE = ("profile.md", Path("profile.md"))
_LIVE_SUGGESTIONS = (
    "profile-suggestions.json",
    Path(_META_DIR) / "profile-suggestions.json",
)
_LIVE_WATERMARK = (
    "profile-distill-state.json",
    Path(_META_DIR) / "profile-distill-state.json",
)

_EXCLUDE_KINDS = ["review", "distill", "eval"]


class RecalibrationScopeError(ValueError):
    """重放范围未指定，或同时指定了两种范围。"""


@dataclass(frozen=True)
class _NullSessionRegistrar:
    """阻止影子重放向任何会话注册表写入。"""

    def register(self, rec: SessionRecord) -> None:
        """重放 agent 不注册为真实会话。"""

    def update_completed(
        self, cc_session_id: str, completed_bytes: int, when: str | None = None
    ) -> None:
        """重放 agent 不更新真实完成水位。"""


_NULL_REGISTRAR = _NullSessionRegistrar()


@dataclass(frozen=True)
class FrozenSession:
    """冻结到已完成 offset 的用户会话。"""

    cc_session_id: str
    end_offset: int
    jsonl_path: str
    jsonl_exists: bool
    registered_at: str


@dataclass(frozen=True)
class LiveHashes:
    """计划时三个 live 文件的摘要；文件缺失时为 None。"""

    profile: str | None
    suggestions: str | None
    watermark: str | None

    def to_manifest_dict(self) -> dict[str, str]:
        return {
            "profile": self.profile or "missing",
            "suggestions": self.suggestions or "missing",
            "watermark": self.watermark or "missing",
        }


@dataclass(frozen=True)
class RecalibrationPlan:
    """一次只读重校准计划及其 live 状态快照。"""

    scope_all: bool
    from_date: str | None
    sessions: tuple[FrozenSession, ...]
    missing_jsonl: tuple[str, ...]
    live_hashes: LiveHashes
    estimated_agent_calls: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": {"all": self.scope_all, "from": self.from_date},
            "sessions": [
                {
                    "cc_session_id": s.cc_session_id,
                    "end_offset": s.end_offset,
                    "jsonl_path": s.jsonl_path,
                    "jsonl_exists": s.jsonl_exists,
                    "registered_at": s.registered_at,
                }
                for s in self.sessions
            ],
            "missing_jsonl": list(self.missing_jsonl),
            "live_hashes": self.live_hashes.to_manifest_dict(),
            "estimated_agent_calls": self.estimated_agent_calls,
        }


@dataclass(frozen=True)
class RecalibrationRunResult:
    """影子重放结果，与落盘的 report.json 对应。"""

    run_id: str
    policy_version: int
    created_at: str
    scope_all: bool
    from_date: str | None
    status: str
    staging_dir: str
    sessions_total: int
    sessions_ok: int
    sessions_failed: int
    failed_session_ids: tuple[str, ...]
    raw_count: int
    accepted_count: int
    by_dimension: dict[str, int]
    body_avg_chars: float
    body_max_chars: int
    gate_drops: dict[str, int]
    staged_suggestions: tuple[Suggestion, ...]

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "policy_version": self.policy_version,
            "created_at": self.created_at,
            "scope": {"all": self.scope_all, "from": self.from_date},
            "status": self.status,
            "sessions_total": self.sessions_total,
            "sessions_ok": self.sessions_ok,
            "sessions_failed": self.sessions_failed,
            "failed_session_ids": list(self.failed_session_ids),
            "raw_count": self.raw_count,
            "accepted_count": self.accepted_count,
            "by_dimension": dict(self.by_dimension),
            "body_avg_chars": self.body_avg_chars,
            "body_max_chars": self.body_max_chars,
            "gate_drops": dict(self.gate_drops),
        }
