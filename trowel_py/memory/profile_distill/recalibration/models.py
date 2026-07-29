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
    """表示重放范围未指定，或同时指定了 all 和 from 两种范围。"""


@dataclass(frozen=True)
class _NullSessionRegistrar:
    """丢弃影子重放的会话注册和完成水位更新。"""

    def register(self, rec: SessionRecord) -> None:
        """忽略一条会话注册请求。

        Args:
            rec: 不会被保存的会话记录。
        """

    def update_completed(
        self, cc_session_id: str, completed_bytes: int, when: str | None = None
    ) -> None:
        """忽略一条完成水位更新请求。

        Args:
            cc_session_id: 不会被更新的 Claude Code 会话 ID。
            completed_bytes: 不会被保存的完成字节数。
            when: 不会被保存的更新时间。
        """


_NULL_REGISTRAR = _NullSessionRegistrar()


@dataclass(frozen=True)
class FrozenSession:
    """重校准计划在计划时刻冻结的用户会话元数据。

    本对象只冻结路径、存在性和 offset 等计划时值，不复制或哈希 JSONL 内容；
    计划后源文件仍可能变化。

    Attributes:
        cc_session_id: 原 Claude Code 会话 ID。
        end_offset: 计划时的最后完成字节偏移；缺失值转为 0。
        jsonl_path: 计划时记录的会话 JSONL 路径，可为空。
        jsonl_exists: 计划时非空路径是否存在；不保证它是普通文件。
        registered_at: 原会话注册时间文本。
    """

    cc_session_id: str
    end_offset: int
    jsonl_path: str
    jsonl_exists: bool
    registered_at: str


@dataclass(frozen=True)
class LiveHashes:
    """计划时三个 live Profile 文件的 SHA-256 摘要。

    Attributes:
        profile: ``profile.md`` 的摘要；文件缺失时为 ``None``。
        suggestions: Profile 建议队列的摘要；文件缺失时为 ``None``。
        watermark: Profile 提炼独立水位的摘要；文件缺失时为 ``None``。
    """

    profile: str | None
    suggestions: str | None
    watermark: str | None

    def to_manifest_dict(self) -> dict[str, str]:
        """把三个摘要转换为 manifest 字段。

        Returns:
            包含 ``profile``、``suggestions`` 和 ``watermark`` 的新字典；
            ``None`` 或空字符串统一写为 ``"missing"``。
        """
        return {
            "profile": self.profile or "missing",
            "suggestions": self.suggestions or "missing",
            "watermark": self.watermark or "missing",
        }


@dataclass(frozen=True)
class RecalibrationPlan:
    """一次只读重校准计划及其 live 状态快照。

    Attributes:
        scope_all: 是否选择全部合格用户会话。
        from_date: from 范围的起始日期；all 范围时为 ``None``。
        sessions: 按计划顺序冻结的会话，包括来源 JSONL 缺失的会话。
        missing_jsonl: 计划时来源路径为空或不存在的会话 ID。
        live_hashes: 计划时三个 live Profile 文件的摘要。
        estimated_agent_calls: ``jsonl_exists=True`` 的会话数。
    """

    scope_all: bool
    from_date: str | None
    sessions: tuple[FrozenSession, ...]
    missing_jsonl: tuple[str, ...]
    live_hashes: LiveHashes
    estimated_agent_calls: int

    def to_dict(self) -> dict[str, Any]:
        """把计划转换为 CLI 可序列化结构。

        Returns:
            包含嵌套 scope、逐会话元数据、缺失来源、live 摘要和预计调用数的
            新字典。
        """
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
    """影子重放的聚合结果。

    ``frozen=True`` 只禁止字段重新绑定；``by_dimension`` 和 ``gate_drops`` 两个
    字典仍可原地修改。``report.json`` 由 ``to_report_dict`` 生成，建议正文
    另存于 ``staged-suggestions.json``。

    Attributes:
        run_id: 本次重放及 staging 目录的标识。
        policy_version: 门禁写入 staged 建议的策略版本。
        created_at: 本次重放使用的创建时间文本。
        scope_all: 是否选择全部合格用户会话。
        from_date: from 范围的起始日期；all 范围时为 ``None``。
        status: ``complete`` 或 ``incomplete``；处理异常或运行期间 live 摘要变化
            时为后者，来源 JSONL 缺失本身不会改为 ``incomplete``。
        staging_dir: 本次隔离产物目录，仅保留在返回对象中。
        sessions_total: 计划中的会话总数，包括来源缺失项。
        sessions_ok: 没有记录错误的会话数。
        sessions_failed: 记录错误的会话数，包括来源缺失和处理失败。
        failed_session_ids: 按计划顺序列出的上述失败会话 ID。
        raw_count: 所有成功门禁统计中 ``raw`` 的总和。
        accepted_count: 全部 staged 建议数。
        by_dimension: staged 建议按维度统计的数量。
        body_avg_chars: staged 正文平均字符数，保留两位小数；没有建议时为 0.0。
        body_max_chars: staged 正文最大字符数；没有建议时为 0。
        gate_drops: 各门禁丢弃原因与超量数的跨会话总和。
        staged_suggestions: 按计划会话和各自门禁输出顺序汇总的建议。
    """

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
        """把聚合统计转换为 ``report.json`` 内容。

        ``staging_dir`` 和 ``staged_suggestions`` 不进入报告；后者单独写入
        ``staged-suggestions.json``。

        Returns:
            包含运行身份、scope、状态、会话计数、门禁统计和正文长度统计的新
            字典。
        """
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
