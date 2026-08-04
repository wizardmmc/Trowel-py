"""驱动隔离 Agent 生成并校验一条可选会话问题。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from trowel_py.memory.daily_review.host_runtime import (
    ReviewHostFactory,
    create_review_host,
    drive_review_host,
    review_derivation,
)
from trowel_py.memory.daily_review.models import ReviewSessionLike
from trowel_py.memory.daily_review.sources import (
    ReviewSource,
    ReviewTargetUnavailable,
    render_review_source,
    resolve_available_review_source,
)
from trowel_py.memory.daily_review.workspace import ensure_review_workdir
from trowel_py.memory.provenance import DerivationProvenance

from .prompt import (
    SESSION_PROBLEM_PIPELINE_VERSION,
    build_session_problem_prompt,
)

_PLACEHOLDER_PROBLEMS = frozenset(
    {"无", "无明确问题", "没有问题", "none", "null", "n/a"}
)
_MAX_PROBLEM_CHARS = 2000


class SessionProblemError(Exception):
    """表示完整会话问题没有产出通过门禁的结果。"""


def _problem_workdir(
    memory_root: Path,
    date_str: str,
    trowel_session_id: str,
) -> Path:
    """返回不直接使用会话 ID 作路径的隔离问题工作目录。"""

    identity = hashlib.sha256(trowel_session_id.encode("utf-8")).hexdigest()[:24]
    return ensure_review_workdir(date_str, memory_root) / "session-problems" / identity


def _read_problem(path: Path) -> tuple[str | None, list[str]]:
    """解析严格单字段 JSON，并返回规范问题或门禁错误。"""

    if not path.exists():
        return None, ["problem.json was not created"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return None, [f"problem.json is malformed: {exc}"]
    if not isinstance(raw, dict) or set(raw) != {"problem"}:
        return None, ["problem.json must contain exactly the problem field"]
    value = raw["problem"]
    if value is None:
        return None, []
    if not isinstance(value, str):
        return None, ["problem must be a string or null"]
    problem = value.strip()
    if not problem:
        return None, ["blank problem must be JSON null"]
    if problem.casefold() in _PLACEHOLDER_PROBLEMS:
        return None, ["placeholder problem must be JSON null"]
    if len(problem) > _MAX_PROBLEM_CHARS:
        return None, [f"problem must not exceed {_MAX_PROBLEM_CHARS} characters"]
    if "\x00" in problem:
        return None, ["problem must not contain NUL"]
    return problem, []


def _revision_prompt(errors: list[str]) -> str:
    """要求同一 Agent 只修正问题 JSON 的结构错误。"""

    details = "\n".join(f"- {error}" for error in errors)
    return (
        "刚写的 problem.json 没通过门禁。只修改当前工作目录的 problem.json；"
        "有明确问题时写一个字符串，没有明确问题时必须写 JSON null。不要写其他文件。"
        f"\n\n【门禁错误】\n{details}"
    )


async def run_session_problem_agent(
    session: ReviewSessionLike,
    date_str: str,
    memory_root: Path,
    *,
    trowel_session_id: str,
    review_source: ReviewSource,
    host_factory: ReviewHostFactory | None = None,
) -> tuple[str | None, DerivationProvenance]:
    """分析完整 Trowel 会话并返回一条问题或明确空结果。

    Args:
        session: 提供原会话工作目录的宿主无关身份。
        date_str: 隔离工作目录使用的关闭日期。
        memory_root: 保存 Review 工作目录的 Memory 根目录。
        trowel_session_id: 用于幂等记录和安全派生工作目录的 Trowel 会话 ID。
        review_source: 只包含当前 Trowel 会话完整内容的来源。
        host_factory: 测试或应用生命周期注入的 Review host 工厂。

    Returns:
        规范问题文本或 None，以及本次生成的 provenance。

    Raises:
        SessionProblemError: 来源不可读、Agent 未正常结束，或两轮输出均不合法。
    """

    try:
        available_source, omitted_context_count = resolve_available_review_source(
            review_source
        )
    except ReviewTargetUnavailable as exc:
        raise SessionProblemError("session problem target is unavailable") from exc
    if omitted_context_count:
        raise SessionProblemError("session problem source cannot omit context")
    workdir = _problem_workdir(memory_root, date_str, trowel_session_id)
    workdir.mkdir(parents=True, exist_ok=True)
    output_path = workdir / "problem.json"
    output_path.unlink(missing_ok=True)
    host = create_review_host(session, workdir, host_factory)
    prompt = build_session_problem_prompt(render_review_source(available_source))
    try:
        if not await drive_review_host(host, prompt):
            raise SessionProblemError("session problem agent did not finish cleanly")
        errors: list[str] = []
        for attempt in range(2):
            problem, errors = _read_problem(output_path)
            if not errors:
                return problem, review_derivation(
                    host,
                    pipeline="memory.session_problem",
                    pipeline_version=SESSION_PROBLEM_PIPELINE_VERSION,
                )
            if attempt == 0 and not await drive_review_host(
                host, _revision_prompt(errors)
            ):
                raise SessionProblemError(
                    "session problem agent did not finish revision cleanly"
                )
        raise SessionProblemError(f"invalid session problem output: {errors}")
    finally:
        close = getattr(host, "close", None)
        if close is not None:
            await close()
