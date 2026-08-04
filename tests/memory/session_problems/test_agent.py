"""验证会话问题 Agent 允许空结果并拒绝伪造占位文本。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trowel_py.memory.daily_review.models import ReviewSession
from trowel_py.memory.daily_review.problems import (
    SessionProblemError,
    run_session_problem_agent,
)
from trowel_py.memory.daily_review.sources import JournalSlice, ReviewSource

FINISHED = SimpleNamespace(type="finished")


class _ProblemHost:
    """把测试指定的两轮 JSON 依次写入问题输出文件。"""

    def __init__(self, workdir: Path, outputs: list[dict[str, object]]) -> None:
        self._workdir = workdir
        self._outputs = outputs
        self.prompts: list[str] = []
        self.model = "glm-5.1"
        self.session_id = "problem-run"

    async def send(self, prompt: str):
        self.prompts.append(prompt)
        output = self._outputs[min(len(self.prompts) - 1, len(self._outputs) - 1)]
        (self._workdir / "problem.json").write_text(
            json.dumps(output, ensure_ascii=False),
            encoding="utf-8",
        )
        yield FINISHED

    async def close(self) -> None:
        """测试 host 不持有外部资源。"""


def _source(tmp_path: Path) -> ReviewSource:
    """用脱敏真实 CC 录制作为完整会话来源。"""

    path = (
        Path(__file__).parents[2]
        / "statistics"
        / "fixtures"
        / "cc-binding-2.1.197.jsonl"
    )
    return ReviewSource(
        host_kind="claude_code",
        context=(),
        target=(JournalSlice(str(path)),),
    )


async def test_problem_agent_accepts_one_problem_and_null(tmp_path: Path) -> None:
    """问题文本和明确 null 都是合法完成结果。"""

    outputs = iter(
        [
            [{"problem": "实现前没有读取真实事件。"}],
            [{"problem": None}],
        ]
    )

    def factory(_session, workdir):
        return _ProblemHost(workdir, next(outputs))

    session = ReviewSession("agent-a", "/isolated/project")
    problem, derivation = await run_session_problem_agent(
        session,
        "2026-08-03",
        tmp_path,
        trowel_session_id="agent-a",
        review_source=_source(tmp_path),
        host_factory=factory,
    )
    empty, _ = await run_session_problem_agent(
        session,
        "2026-08-03",
        tmp_path,
        trowel_session_id="agent-b",
        review_source=_source(tmp_path),
        host_factory=factory,
    )

    assert problem == "实现前没有读取真实事件。"
    assert empty is None
    assert derivation.pipeline == "memory.session_problem"


async def test_problem_agent_revises_placeholder_instead_of_inventing(
    tmp_path: Path,
) -> None:
    """“无明确问题”必须修订为 JSON null，不能作为问题文本展示。"""

    holder: dict[str, _ProblemHost] = {}

    def factory(_session, workdir):
        host = _ProblemHost(
            workdir,
            [{"problem": "无明确问题"}, {"problem": None}],
        )
        holder["host"] = host
        return host

    problem, _ = await run_session_problem_agent(
        ReviewSession("agent-a", "/isolated/project"),
        "2026-08-03",
        tmp_path,
        trowel_session_id="agent-a",
        review_source=_source(tmp_path),
        host_factory=factory,
    )

    assert problem is None
    assert len(holder["host"].prompts) == 2
    assert "null" in holder["host"].prompts[1]


async def test_problem_agent_rejects_second_invalid_output(tmp_path: Path) -> None:
    """两轮都不符合单字段契约时保持请求可重试。"""

    with pytest.raises(SessionProblemError):
        await run_session_problem_agent(
            ReviewSession("agent-a", "/isolated/project"),
            "2026-08-03",
            tmp_path,
            trowel_session_id="agent-a",
            review_source=_source(tmp_path),
            host_factory=lambda _session, workdir: _ProblemHost(
                workdir,
                [{"problem": ["不是字符串"]}],
            ),
        )
