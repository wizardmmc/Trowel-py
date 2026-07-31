"""在独立 staging 目录重放画像提炼并写入可审计产物。

本模块不主动更新 live Profile、建议队列、水位或 sessions 数据库；运行期间
检测到 live 文件变化时，只把结果标记为不完整。
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.profile.distill import DistillError, GateStats, drive_and_gate
from trowel_py.profile.distill.prompt import build_distill_prompt
from trowel_py.profile.models import Profile, Suggestion
from trowel_py.profile.suggestions import (
    PROFILE_DISTILL_POLICY_VERSION,
    suggestion_to_dict,
)

from trowel_py.profile.recalibration.models import (
    _BASELINE_DIR,
    _LIVE_PROFILE,
    _LIVE_SUGGESTIONS,
    _LIVE_WATERMARK,
    _MANIFEST_FILE,
    _META_DIR,
    _NULL_REGISTRAR,
    _RECALIBRATION_DIR,
    _REPORT_FILE,
    _STAGED_FILE,
    _WORK_DIR,
    FrozenSession,
    LiveHashes,
    RecalibrationRunResult,
    ReplayHostFactory,
)
from trowel_py.profile.recalibration.plan import (
    _live_hashes,
    _validate_scope,
    plan_recalibration,
)

logger = logging.getLogger(__name__)


def _copy_live_to_baseline(root: Path, baseline: Path) -> None:
    """把调用时存在的三个 live 文件复制到 baseline。

    缺失文件不创建占位，已有目标会被覆盖；函数不会清理 baseline 中的旧文件。

    Args:
        root: live Profile、建议队列和水位所在的 Memory 根目录。
        baseline: 保存同名基线副本的目录。

    Raises:
        OSError: 无法创建目录、读取源文件或写入副本。
    """
    baseline.mkdir(parents=True, exist_ok=True)
    for _name, rel in (_LIVE_PROFILE, _LIVE_SUGGESTIONS, _LIVE_WATERMARK):
        src = root / rel
        if src.exists():
            shutil.copy2(src, baseline / _name)


def _manifest(
    *,
    run_id: str,
    created_at: str,
    scope_all: bool,
    from_date: str | None,
    live_hashes: LiveHashes,
    sessions: tuple[FrozenSession, ...],
    status: str,
    live_changed_during_run: bool = False,
) -> dict[str, Any]:
    """组装 ``manifest.json`` 的内存结构。

    会话条目只记录 ID、冻结 offset 和 JSONL 路径；输入摘要始终使用计划阶段
    的 live 摘要。

    Args:
        run_id: 本次重放的标识。
        created_at: 本次重放使用的创建时间文本。
        scope_all: 是否选择全部候选会话。
        from_date: from 范围的起始日期；all 范围时为 ``None``。
        live_hashes: 计划阶段冻结的 live 文件摘要。
        sessions: 按计划顺序冻结的会话。
        status: 要写入 manifest 的运行状态。
        live_changed_during_run: 运行期间 live 摘要是否变化。

    Returns:
        可直接序列化为 manifest 的新字典。
    """
    return {
        "run_id": run_id,
        "policy_version": PROFILE_DISTILL_POLICY_VERSION,
        "created_at": created_at,
        "scope": {"all": scope_all, "from": from_date},
        "source_hashes": live_hashes.to_manifest_dict(),
        "sessions": [
            {
                "cc_session_id": s.cc_session_id,
                "end_offset": s.end_offset,
                "jsonl_path": s.jsonl_path,
            }
            for s in sessions
        ],
        "status": status,
        "live_changed_during_run": live_changed_during_run,
    }


def _aggregate_report(
    *,
    run_id: str,
    created_at: str,
    scope_all: bool,
    from_date: str | None,
    status: str,
    staging_dir: str,
    outcomes: list[tuple[FrozenSession, tuple[Suggestion, ...], GateStats, str]],
) -> RecalibrationRunResult:
    """按计划顺序聚合逐会话结果、门禁统计和 staged 建议。

    outcome 的错误文本非空即计为失败；因此缺失 JSONL 也进入失败会话统计。
    ``status`` 由调用方决定，不根据失败计数重新推导。

    Args:
        run_id: 本次重放的标识。
        created_at: 本次重放使用的创建时间文本。
        scope_all: 是否选择全部候选会话。
        from_date: from 范围的起始日期；all 范围时为 ``None``。
        status: 原样写入结果的运行状态。
        staging_dir: 返回给调用方的隔离产物目录文本。
        outcomes: 依次包含冻结会话、接受建议、门禁统计和错误文本的结果。

    Returns:
        会话成败、门禁丢弃项、建议维度和正文长度的聚合结果。
    """
    failed = [s.cc_session_id for (s, _a, _st, err) in outcomes if err]
    ok = sum(1 for (_s, _a, _st, err) in outcomes if not err)
    staged: list[Suggestion] = []
    for _s, accepted, _st, _err in outcomes:
        staged.extend(accepted)

    by_dimension: dict[str, int] = {}
    for s in staged:
        by_dimension[s.dimension] = by_dimension.get(s.dimension, 0) + 1
    body_lens = [len(s.body) for s in staged]
    raw = sum(st.raw for (_s, _a, st, _e) in outcomes)
    gate_drops = {
        "dropped_empty_body": sum(st.dropped_empty_body for (_s, _a, st, _e) in outcomes),
        "dropped_too_long": sum(st.dropped_too_long for (_s, _a, st, _e) in outcomes),
        "dropped_no_evidence": sum(st.dropped_no_evidence for (_s, _a, st, _e) in outcomes),
        "over_limit": sum(st.over_limit for (_s, _a, st, _e) in outcomes),
    }
    return RecalibrationRunResult(
        run_id=run_id,
        policy_version=PROFILE_DISTILL_POLICY_VERSION,
        created_at=created_at,
        scope_all=scope_all,
        from_date=from_date,
        status=status,
        staging_dir=staging_dir,
        sessions_total=len(outcomes),
        sessions_ok=ok,
        sessions_failed=len(failed),
        failed_session_ids=tuple(failed),
        raw_count=raw,
        accepted_count=len(staged),
        by_dimension=by_dimension,
        body_avg_chars=round(sum(body_lens) / len(body_lens), 2) if body_lens else 0.0,
        body_max_chars=max(body_lens) if body_lens else 0,
        gate_drops=gate_drops,
        staged_suggestions=tuple(staged),
    )


async def run_recalibration(
    root: Path,
    *,
    scope_all: bool = False,
    from_date: str | None = None,
    proxy_base_url: str,
    settings_path: Path | str | None = None,
    host_factory: ReplayHostFactory | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
) -> RecalibrationRunResult:
    """在隔离 staging 目录中顺序重放冻结的用户会话。

    函数先生成只读计划，再复制当时存在的 live 文件并写入 ``running``
    manifest。每个可用会话都从空 Profile 开始，只用本轮此前接受的建议去重；
    接受项不会写回 live 队列。缺失 JSONL 会计入失败会话，但本身不会把运行
    状态改为 ``incomplete``。

    agent 或门禁在 ``drive_and_gate`` 内抛出的异常会转成单会话失败，并继续
    后续会话。会话准备、live 摘要或产物写入异常则向上传播，可能留下
    ``running`` manifest 或部分产物。最终 manifest、staged 建议和报告依次
    覆盖写入，不构成事务。运行结束时只要有处理异常或 live 摘要变化，状态
    就是 ``incomplete``。

    Args:
        root: sessions 数据库、live 文件和 staging 目录所在的 Memory 根目录。
        scope_all: 是否选择全部候选会话。
        from_date: from 范围的起始日期；使用该范围时必须不是 ``None``。
        proxy_base_url: 传给默认 CCHost 的非空代理地址；自定义 factory 自行
            处理运行时配置。
        settings_path: 传给默认 CCHost 的 provider settings 路径。
        host_factory: 可选的测试或替代 host 构造器。
        run_id: ``None`` 或空字符串时生成 UUID；其他文本未经校验便参与路径
            拼接，绝对路径或 ``..`` 可越出 staging 根目录，复用旧值还会沿用
            旧 baseline、工作目录和草稿。
        created_at: ``None`` 或空字符串时使用当前本地时间的 ISO 文本；其他
            文本不校验，前 10 个字符直接作为建议日期。

    Returns:
        聚合后的运行状态、会话统计、门禁统计和 staged 建议。

    Raises:
        RecalibrationScopeError: all 和 from 范围同时指定或均未指定。
        ValueError: ``proxy_base_url`` 为空。
        OSError: 无法检查输入路径、复制 live 文件、创建目录或写入产物。
        sqlite3.Error: sessions 数据库无法只读打开或查询。
    """
    _validate_scope(scope_all=scope_all, from_date=from_date)
    if not proxy_base_url:
        raise ValueError("--run requires --proxy-base-url (never bypass the proxy)")

    plan = plan_recalibration(root, scope_all=scope_all, from_date=from_date)
    rid = run_id or uuid.uuid4().hex
    stamp = created_at or datetime.now().isoformat()

    staging = root / _META_DIR / _RECALIBRATION_DIR / rid
    baseline = staging / _BASELINE_DIR
    work_root = staging / _WORK_DIR
    staging.mkdir(parents=True, exist_ok=True)
    _copy_live_to_baseline(root, baseline)

    # 先写 running，使会话处理结束前的中断保留未完成状态。
    manifest_path = staging / _MANIFEST_FILE
    manifest_path.write_text(
        json.dumps(
            _manifest(
                run_id=rid,
                created_at=stamp,
                scope_all=scope_all,
                from_date=from_date,
                live_hashes=plan.live_hashes,
                sessions=plan.sessions,
                status="running",
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    outcomes: list[tuple[FrozenSession, tuple[Suggestion, ...], GateStats, str]] = []
    staged_so_far: list[Suggestion] = []
    had_failure = False
    for frozen in plan.sessions:
        if not frozen.jsonl_exists:
            # 来源缺失进入失败统计，但不单独把运行状态改为 incomplete。
            outcomes.append((frozen, (), GateStats(), "missing jsonl"))
            continue
        workdir = work_root / frozen.cc_session_id
        workdir.mkdir(parents=True, exist_ok=True)
        prompt = build_distill_prompt(
            frozen.jsonl_path,
            list(staged_so_far),
            Profile(),
            start_offset=None,
            end_offset=frozen.end_offset,
        )
        try:
            gated = await drive_and_gate(
                frozen.cc_session_id,
                workdir,
                prompt,
                proxy_base_url=proxy_base_url,
                settings_path=settings_path,
                host_factory=host_factory,
                date_str=stamp[:10],
                # 默认 CCHost 使用空 registrar，避免重放会话写入 sessions.db。
                session_registrar=_NULL_REGISTRAR,
            )
        except DistillError as exc:
            had_failure = True
            logger.warning(
                "recalibrate: session %s failed (run marked incomplete): %s",
                frozen.cc_session_id,
                exc,
            )
            outcomes.append((frozen, (), GateStats(), str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001
            # 未预期的 drive_and_gate 异常也记为单会话失败，以便继续后续会话。
            had_failure = True
            logger.exception(
                "recalibrate: unexpected error on %s (run marked incomplete)",
                frozen.cc_session_id,
            )
            outcomes.append((frozen, (), GateStats(), f"unexpected: {exc}"))
            continue
        accepted = gated.accepted
        staged_so_far.extend(accepted)
        outcomes.append((frozen, accepted, gated.stats, ""))

    after_hashes = _live_hashes(root)
    live_changed_during_run = after_hashes != plan.live_hashes
    status = "incomplete" if (had_failure or live_changed_during_run) else "complete"
    result = _aggregate_report(
        run_id=rid,
        created_at=stamp,
        scope_all=scope_all,
        from_date=from_date,
        status=status,
        staging_dir=str(staging),
        outcomes=outcomes,
    )

    manifest_path.write_text(
        json.dumps(
            _manifest(
                run_id=rid,
                created_at=stamp,
                scope_all=scope_all,
                from_date=from_date,
                live_hashes=plan.live_hashes,
                sessions=plan.sessions,
                status=status,
                live_changed_during_run=live_changed_during_run,
            ),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (staging / _STAGED_FILE).write_text(
        json.dumps(
            {"suggestions": [suggestion_to_dict(s) for s in result.staged_suggestions]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (staging / _REPORT_FILE).write_text(
        json.dumps(result.to_report_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result
