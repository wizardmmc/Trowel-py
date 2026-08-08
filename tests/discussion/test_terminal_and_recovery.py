"""验证根终态判定和应用重启后的同轮新 attempt 恢复。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.events import AttemptLiveEventPublisher, DiscussionEventBus
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.errors import DiscussionRuntimeError, DiscussionStateError
from trowel_py.discussion.models import ParticipantAttemptHistoryRequest
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import (
    ContinueDiscussionRequest,
    CreateDiscussionRequest,
    VersionedCommand,
)
from trowel_py.discussion.service import DiscussionService
from tests.discussion.support import (
    FakeConfigurationCatalog,
    FakeParticipantSessions,
)


class OldTerminalSessions(FakeParticipantSessions):
    """让第一位 participant 收到旧 finished 后静默 EOF。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """坏 participant 不提供当前根终态，其他 participant 正常完成。

        Args:
            agent_session_id: 当前测试会话 ID。
            prompt: 公共输入。

        Returns:
            可暴露旧 terminal 误归属的事件流。
        """

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送当前 start/text、旧 finished 后结束。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            current = "turn-current"
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "seq": 1,
                "type": "turn_start",
                "turn_id": current,
                "thread_id": None,
                "payload": {},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "seq": 2,
                "type": "text",
                "turn_id": current,
                "thread_id": None,
                "payload": {"text": "partial-must-not-publish"},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "seq": 3,
                "type": "finished",
                "turn_id": "turn-old",
                "thread_id": None,
                "payload": {},
            }

        return generate()


class StartupErrorSessions(FakeParticipantSessions):
    """让第一位 participant 在根 turn 建立前返回运行时占用错误。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """bad 返回真实 rootless error，其他 participant 正常完成。"""

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送与真实 CCHost turn_in_progress 同形的启动错误。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "seq": 1,
                "type": "error",
                "turn_id": None,
                "thread_id": None,
                "payload": {"subclass": "turn_in_progress"},
            }

        return generate()


class HangingSessions(FakeParticipantSessions):
    """让 bad 在 root text 后等待，good 正常成功后再模拟应用退出。"""

    def __init__(self) -> None:
        """创建由测试取消而不是 Event 释放的端口。"""

        super().__init__()
        self.bad_started = asyncio.Event()

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """发出部分文本后等待一个永不置位的本地 Event。

        Args:
            agent_session_id: 当前测试会话 ID。
            prompt: 公共输入。

        Returns:
            只会被 coordinator.stop 取消的事件流。
        """

        if self.names[agent_session_id] == "good":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """生成未形成终态的当前根 turn。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            turn_id = f"turn-{agent_session_id}-crash"
            self.bad_started.set()
            yield {
                "session_id": agent_session_id,
                "runtime": self._bindings[agent_session_id].runtime.value,
                "seq": 1,
                "type": "turn_start",
                "turn_id": turn_id,
                "thread_id": self._bindings[agent_session_id].native_session_id,
                "payload": {},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": self._bindings[agent_session_id].runtime.value,
                "seq": 2,
                "type": "text",
                "turn_id": turn_id,
                "thread_id": self._bindings[agent_session_id].native_session_id,
                "payload": {"text": "old-partial-must-never-publish"},
            }
            await asyncio.Event().wait()

        return generate()


class OneDoneSessions(FakeParticipantSessions):
    """让 good 确定性完成，同时让 bad 等待测试释放。"""

    def __init__(self) -> None:
        """创建快槽完成信号和慢槽释放屏障。"""

        super().__init__()
        self.good_finished = asyncio.Event()
        self.bad_started = asyncio.Event()
        self.bad_release = asyncio.Event()

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """按参与者名称返回快完成或可释放的慢事件流。

        Args:
            agent_session_id: 当前测试会话 ID。
            prompt: 协调器实际发送的公共输入。

        Returns:
            good 立即结束、bad 等待 ``bad_release`` 的真实事件形状。
        """

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """生成不依赖任务调度先后顺序的两种事件流。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            name = self.names[agent_session_id]
            binding = self._bindings[agent_session_id]
            turn_id = f"turn-one-done-{name}"
            common = {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "turn_id": turn_id,
                "thread_id": (
                    binding.native_session_id
                    if binding.runtime.value == "codex"
                    else None
                ),
            }
            yield {**common, "seq": 1, "type": "turn_start", "payload": {}}
            if name == "bad":
                self.bad_started.set()
                await self.bad_release.wait()
            yield {
                **common,
                "seq": 2,
                "type": "text",
                "payload": {"text": f"answer-{name}"},
            }
            yield {
                **common,
                "seq": 3,
                "type": "finished",
                "payload": {"duration_ms": 1},
            }
            if name == "good":
                self.good_finished.set()

        return generate()


class MixedTerminalSessions(FakeParticipantSessions):
    """按 participant 名称生成限额、runtime 超时、普通错误和成功终态。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """为错误 participant 发送当前根 error，success 走正常事件流。"""

        name = self.names[agent_session_id]
        if name == "success":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送与当前会话和根 turn 匹配的稳定错误事件。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            turn_id = f"turn-{name}"
            binding = self._bindings[agent_session_id]
            yield {
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "type": "turn_start",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": {},
            }
            payload_by_name = {
                "limited": {"code": 429},
                "runtime-timeout": {"message": "upstream timed out"},
                "failed": {"message": "provider rejected request"},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "type": "error",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": payload_by_name[name],
            }

        return generate()


class ProvisionFailsOnceSessions(FakeParticipantSessions):
    """第一次创建 participant binding 失败，模拟创建 saga 在首轮前中断。"""

    def __init__(self) -> None:
        """创建只失败一次的 participant 端口。"""

        super().__init__()
        self.failures = 0

    async def ensure_session(
        self,
        participant,
        *,
        workdir: str,
    ):
        """第一次抛出运行时错误，后续按正常端口 provision。

        Args:
            participant: 当前参与者。
            workdir: 冻结工作目录。
        """

        if self.failures == 0:
            self.failures += 1
            raise RuntimeError("injected provisioning failure")
        return await super().ensure_session(participant, workdir=workdir)


class ElicitingSessions(FakeParticipantSessions):
    """让第一位 participant 先 AskUserQuestion，再在内部 deny 后继续完成。"""

    def __init__(self) -> None:
        """创建记录被拒绝提问会话的测试端口。"""

        super().__init__()
        self.cancelled_elicitations: list[str] = []

    async def cancel_elicitation(self, agent_session_id: str) -> bool:
        """记录协调器内部拒绝并立即确认控制消息写入。"""

        self.cancelled_elicitations.append(agent_session_id)
        return True

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """bad 先发交互请求，其他 participant 沿用正常成功流。"""

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """只有收到内部 deny 后才继续产出文字和完成终态。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            turn_id = "turn-elicit"
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "turn_start",
                "turn_id": turn_id,
                "thread_id": None,
                "payload": {},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "elicit_request",
                "turn_id": turn_id,
                "thread_id": None,
                "payload": {"request_id": "question-1"},
            }
            if agent_session_id not in self.cancelled_elicitations:
                await asyncio.Event().wait()
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "text",
                "turn_id": turn_id,
                "thread_id": None,
                "payload": {"text": "无需用户回答，继续形成结论"},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "finished",
                "turn_id": turn_id,
                "thread_id": None,
                "payload": {},
            }

        return generate()


class UserAnsweredSessions(FakeParticipantSessions):
    """让 bad 发出真实 AskUserQuestion，并等待 discussion 专用回答。"""

    def __init__(self) -> None:
        """创建提问已发出、回答已送达两个同步点。"""

        super().__init__()
        self.question_ready = asyncio.Event()
        self.answer_ready = asyncio.Event()
        self.received_answers: dict[str, str] | None = None

    async def answer_elicitation(
        self,
        agent_session_id: str,
        answers: dict[str, str],
    ) -> bool:
        """只接受 bad 的回答并释放其原生事件流。"""

        assert self.names[agent_session_id] == "bad"
        self.received_answers = dict(answers)
        self.answer_ready.set()
        return True

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """bad 等待答案，good 沿用普通成功流。"""

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发出 AskUserQuestion，收到回答后给出最终结论。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "turn_start",
                "turn_id": "turn-user-answer",
                "thread_id": None,
                "payload": {},
            }
            self.question_ready.set()
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "elicit_request",
                "turn_id": "turn-user-answer",
                "thread_id": None,
                "payload": {
                    "request_id": "question-user-1",
                    "tool_name": "AskUserQuestion",
                    "questions": [{"question": "采用哪种口径？"}],
                },
            }
            await self.answer_ready.wait()
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "text",
                "turn_id": "turn-user-answer",
                "thread_id": None,
                "payload": {"text": "采用用户选择的口径"},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "type": "finished",
                "turn_id": "turn-user-answer",
                "thread_id": None,
                "payload": {},
            }

        return generate()


class IntermediateTextSessions(FakeParticipantSessions):
    """让 bad 在工具前后都输出文字，用于验证最终后缀。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """bad 产生中间解释、工具活动和最终总结。"""

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送一条可区分工作文字与最终文字的事件流。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            common = {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "turn_id": "turn-final-suffix",
                "thread_id": None,
            }
            yield {**common, "type": "turn_start", "payload": {}}
            yield {**common, "type": "text", "payload": {"text": "我先检查。"}}
            yield {
                **common,
                "type": "tool_call",
                "item_id": "tool-1",
                "payload": {"tool_name": "Read", "input": {}},
            }
            yield {
                **common,
                "type": "tool_result",
                "item_id": "tool-1",
                "payload": {"tool_use_id": "tool-1", "content": "ok"},
            }
            yield {**common, "type": "text", "payload": {"text": "最终结论"}}
            yield {**common, "type": "finished", "payload": {}}

        return generate()


class NoTrailingFinalTextSessions(FakeParticipantSessions):
    """让 bad 以工具结果收尾，用于钉死空最终回答边界。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """bad 只有工具前解释，不在最后工作事件后提供最终文字。"""

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送 text→tool→finished，确保中间文字不会冒充最终回答。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            common = {
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "turn_id": "turn-no-final-text",
                "thread_id": None,
            }
            yield {**common, "type": "turn_start", "payload": {}}
            yield {**common, "type": "text", "payload": {"text": "我先检查。"}}
            yield {
                **common,
                "type": "tool_call",
                "item_id": "tool-no-final",
                "payload": {"tool_name": "Read", "input": {}},
            }
            yield {
                **common,
                "type": "tool_result",
                "item_id": "tool-no-final",
                "payload": {"tool_use_id": "tool-no-final", "content": "ok"},
            }
            yield {**common, "type": "finished", "payload": {}}

        return generate()


class PostTerminalRuntimeErrorSessions(FakeParticipantSessions):
    """让 bad 在完整成功终态之后模拟 drain 阶段断链。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """终态前正常产出，终态后才抛出运行时连接错误。"""

        if self.names[agent_session_id] != "bad":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送完整回答和 finished 后模拟 drain 断链。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            common = {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": "claude_code",
                "turn_id": "turn-post-terminal-error",
                "thread_id": None,
                "item_id": None,
            }
            yield {**common, "seq": 1, "type": "turn_start", "payload": {}}
            yield {
                **common,
                "seq": 2,
                "type": "text",
                "payload": {"text": "已经形成的完整回答"},
            }
            yield {**common, "seq": 3, "type": "finished", "payload": {}}
            raise DiscussionRuntimeError("终态后的 drain 连接断开")

        return generate()


class FailAndHangSessions(FakeParticipantSessions):
    """一方本地失败时让另一方保持活动，用来验证 sibling 收敛。"""

    def __init__(self) -> None:
        """创建首轮故障屏障和残余 worker 观测字段。"""

        super().__init__()
        self.hanging_started = asyncio.Event()
        self.hanging_exited = asyncio.Event()
        self.fail_once = True
        self.interrupted_sessions: list[str] = []

    async def interrupt(self, agent_session_id: str) -> None:
        """记录 TaskGroup 取消 sibling 时发出的 runtime 中断。"""

        self.interrupted_sessions.append(agent_session_id)

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """首轮 bad 等 good 活跃后抛错；恢复轮全部正常完成。"""

        if not self.fail_once:
            return super().run_turn(agent_session_id, prompt)
        if self.names[agent_session_id] == "bad":

            async def fail() -> AsyncIterator[dict[str, Any]]:
                """等待 sibling 已进入流后抛出本地程序错误。"""

                await self.hanging_started.wait()
                self.fail_once = False
                if False:
                    yield {}
                raise ValueError("injected one-worker local failure")

            return fail()

        async def hang() -> AsyncIterator[dict[str, Any]]:
            """保持 runtime turn 活动，直到 TaskGroup 取消并等待 finally。"""

            self.hanging_started.set()
            try:
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                self.hanging_exited.set()

        return hang()


class ApprovingSessions(FakeParticipantSessions):
    """让 Codex participant 发出审批，并在内部 decline 后继续。"""

    def __init__(self) -> None:
        """创建审批拒绝记录。"""

        super().__init__()
        self.declined: list[tuple[str, str]] = []

    def decline_approval(self, agent_session_id: str, request_id: str) -> None:
        """记录协调器提交的立即拒绝决定。"""

        self.declined.append((agent_session_id, request_id))

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """good 发出 approval_request，bad 沿用正常成功流。"""

        if self.names[agent_session_id] != "good":
            return super().run_turn(agent_session_id, prompt)

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """只有内部 decline 后才继续形成普通文字结论。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            binding = self._bindings[agent_session_id]
            turn_id = "turn-approval"
            yield {
                "session_id": agent_session_id,
                "runtime": "codex",
                "type": "turn_start",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": {},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "codex",
                "type": "approval_request",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": {"request_id": "approval-1", "status": "pending"},
            }
            if (agent_session_id, "approval-1") not in self.declined:
                await asyncio.Event().wait()
            yield {
                "session_id": agent_session_id,
                "runtime": "codex",
                "type": "approval_request",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": {"request_id": "approval-1", "status": "answered"},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "codex",
                "type": "text",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": {"text": "审批被拒绝后仍可给出只读分析"},
            }
            yield {
                "session_id": agent_session_id,
                "runtime": "codex",
                "type": "finished",
                "turn_id": turn_id,
                "thread_id": binding.native_session_id,
                "payload": {},
            }

        return generate()


class UsageReportingSessions(FakeParticipantSessions):
    """模拟 Claude 实时 usage 为零、原生历史保留真实用量的差异。"""

    async def read_attempt_history(
        self,
        request: ParticipantAttemptHistoryRequest,
    ) -> list[dict[str, Any]]:
        """为 Claude 返回 transcript 回放中的真实 message usage。"""

        events = await super().read_attempt_history(request)
        if request.runtime.value != "claude_code":
            return events
        return [
            *events,
            {
                "schema": "agent-event-v1",
                "session_id": request.agent_session_id,
                "runtime": "claude_code",
                "seq": 3,
                "type": "context_usage",
                "turn_id": request.root_turn_id,
                "item_id": None,
                "payload": {
                    "message_id": "msg-real-shape",
                    "model": "glm-5.2",
                    "usage": {
                        "input_tokens": 11086,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 23104,
                        "output_tokens": 3186,
                    },
                },
            },
        ]

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """依 participant runtime 发送 Claude 或 Codex 用量事件。"""

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """发送可成功发布且带真实 usage shape 的单轮事件。"""

            self.prompts.setdefault(agent_session_id, []).append(prompt)
            binding = self._bindings[agent_session_id]
            turn_id = f"turn-{agent_session_id}-usage"
            common = {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "turn_id": turn_id,
                "thread_id": (
                    binding.native_session_id
                    if binding.runtime.value == "codex"
                    else None
                ),
                "item_id": None,
            }
            yield {**common, "seq": 1, "type": "turn_start", "payload": {}}
            yield {
                **common,
                "seq": 2,
                "type": "text",
                "payload": {"text": f"usage-{binding.runtime.value}"},
            }
            if binding.runtime.value == "claude_code":
                yield {
                    **common,
                    "seq": 3,
                    "type": "context_usage",
                    "payload": {
                        "message_id": "msg-real-shape",
                        "model": "glm-5.2",
                        "usage": {
                            "input_tokens": 0,
                            "output_tokens": 0,
                        },
                    },
                }
            else:
                yield {
                    **common,
                    "seq": 3,
                    "type": "usage_updated",
                    "payload": {
                        "total": {
                            "totalTokens": 52817,
                            "inputTokens": 50311,
                            "cachedInputTokens": 40192,
                            "outputTokens": 2506,
                            "reasoningOutputTokens": 1536,
                        },
                        "last": {
                            "totalTokens": 16707,
                            "inputTokens": 15791,
                            "cachedInputTokens": 12416,
                            "outputTokens": 916,
                            "reasoningOutputTokens": 512,
                        },
                        "model_context_window": 258400,
                    },
                }
            yield {**common, "seq": 4, "type": "finished", "payload": {}}

        return generate()


def _system(
    tmp_path: Path,
    sessions: FakeParticipantSessions,
) -> tuple[DiscussionService, DiscussionCoordinator, Any, DiscussionArtifactStore]:
    """装配共享临时 DB 的 service/coordinator 并返回 opener。

    Args:
        tmp_path: 当前测试隔离目录。
        sessions: 本次应用进程使用的 participant port。

    Returns:
        service、coordinator、repository opener 和 artifact store。
    """

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开共享测试数据库。"""

        return open_discussion_repository(db_path)

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    events = DiscussionEventBus()
    episode_writer = DiscussionEpisodeWriter(
        artifacts,
        memory_root=tmp_path / "memory",
    )
    coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        sessions,
        events,
        episode_writer,
    )
    service = DiscussionService(
        opener,
        artifacts,
        coordinator,
        events,
        FakeConfigurationCatalog(),
    )
    return service, coordinator, opener, artifacts


def _request(request_id: str) -> CreateDiscussionRequest:
    """创建两 participant、每轮用户参与的研讨请求。

    Args:
        request_id: 当前测试唯一创建身份。
    """

    return CreateDiscussionRequest(
        request_id=request_id,
        topic="检查终态",
        workdir="/tmp",
        progression_mode="user_guided",
        participants=[
            {"name": "bad", "session_configuration_id": "cc-bad"},
            {"name": "good", "session_configuration_id": "codex-good"},
        ],
    )


def _mixed_request() -> CreateDiscussionRequest:
    """创建覆盖四种 runtime 终态的用户参与模式研讨。"""

    return CreateDiscussionRequest(
        request_id="mixed-terminals",
        topic="检查混合终态仍能共同发布",
        workdir="/tmp",
        progression_mode="user_guided",
        participants=[
            {"name": "success", "session_configuration_id": "cc-success"},
            {"name": "limited", "session_configuration_id": "codex-limited"},
            {
                "name": "runtime-timeout",
                "session_configuration_id": "cc-timeout",
            },
            {"name": "failed", "session_configuration_id": "codex-failed"},
        ],
    )


@pytest.mark.anyio
async def test_old_finished_and_silent_eof_cannot_publish_partial_as_success(
    tmp_path: Path,
) -> None:
    sessions = OldTerminalSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("old-terminal"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )

    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)
    published = service.get(created["id"])
    slots = published["rounds"][0]["participants"]

    assert slots[0]["status"] == "host_lost"
    assert slots[0]["content"] is None
    assert slots[0]["error_code"] == "STREAM_ENDED_WITHOUT_TERMINAL"
    assert "partial-must-not-publish" not in str(published)
    assert slots[1]["status"] == "succeeded"


@pytest.mark.anyio
async def test_error_before_turn_start_keeps_real_runtime_reason(
    tmp_path: Path,
) -> None:
    """启动错误不应被丢弃并改写成没有收到终态。"""

    sessions = StartupErrorSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("startup-error"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )

    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)
    slot = service.get(created["id"])["rounds"][0]["participants"][0]

    assert slot["status"] == "failed"
    assert slot["error_code"] == "TURN_IN_PROGRESS"
    assert slot["error_message"] == "参与者上一轮仍在收尾，本轮输入没有发送"


@pytest.mark.anyio
async def test_elicitation_is_denied_internally_and_round_does_not_hang(
    tmp_path: Path,
) -> None:
    """私有 participant 的 AskUserQuestion 无公开回答入口，也不能永久卡轮。"""

    sessions = ElicitingSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("elicit"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )

    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)
    published = service.get(created["id"])

    assert published["rounds"][0]["status"] == "published"
    assert len(sessions.cancelled_elicitations) == 1
    assert published["rounds"][0]["participants"][0]["content"] == (
        "无需用户回答，继续形成结论"
    )


@pytest.mark.anyio
async def test_ask_user_question_waits_for_scoped_user_answer(
    tmp_path: Path,
) -> None:
    """AskUserQuestion 只暂停发问方，相同回答重复提交保持幂等。"""

    sessions = UserAnsweredSessions()
    service, coordinator, opener, _ = _system(tmp_path, sessions)
    created = await service.create(_request("user-answer"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(sessions.question_ready.wait(), timeout=1)
    while True:
        with opener() as repository:
            row = repository.connection.execute(
                """
                SELECT a.id, p.id AS participant_id
                FROM discussion_attempts a
                JOIN discussion_participants p ON p.id=a.participant_id
                WHERE p.name='bad' AND a.status='running'
                """
            ).fetchone()
        if row is not None:
            break
        await asyncio.sleep(0)
    answers = {"采用哪种口径？": "保守口径"}
    request = dict(
        discussion_id=created["id"],
        participant_id=str(row["participant_id"]),
        attempt_id=str(row["id"]),
        request_id="question-user-1",
        answers=answers,
    )

    assert await coordinator.answer_elicitation(**request) is True
    assert await coordinator.answer_elicitation(**request) is True
    with pytest.raises(DiscussionStateError, match="另一份答案"):
        await coordinator.answer_elicitation(
            **{**request, "answers": {"采用哪种口径？": "激进口径"}}
        )
    with opener() as repository:
        other_participant_id = repository.connection.execute(
            "SELECT id FROM discussion_participants WHERE id != ? LIMIT 1",
            (str(row["participant_id"]),),
        ).fetchone()["id"]
    with pytest.raises(DiscussionStateError):
        await coordinator.answer_elicitation(
            **{**request, "participant_id": str(other_participant_id)}
        )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)

    assert sessions.received_answers == answers
    assert service.get(created["id"])["rounds"][0]["participants"][0]["content"] == (
        "采用用户选择的口径"
    )


@pytest.mark.anyio
async def test_only_trailing_text_after_last_work_event_is_published(
    tmp_path: Path,
) -> None:
    """工具前的中间解释留在轨迹，不重复写进共同公开正文。"""

    sessions = IntermediateTextSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("final-suffix"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)

    content = service.get(created["id"])["rounds"][0]["participants"][0]["content"]
    assert content == "最终结论"


@pytest.mark.anyio
async def test_one_finished_participant_wakes_observer_before_round_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一路成功、一路仍运行时也要通知前端进入单卡 Worked 终态。"""

    sessions = OneDoneSessions()
    service, coordinator, opener, _ = _system(tmp_path, sessions)
    created = await service.create(_request("one-done-wakeup"))
    original_publish = coordinator._events.publish
    published_discussion_ids: list[str] = []

    def record_publish(discussion_id: str) -> None:
        """记录持久状态唤醒，不在 repository 事务回调里重入数据库。"""

        published_discussion_ids.append(discussion_id)
        original_publish(discussion_id)

    monkeypatch.setattr(coordinator._events, "publish", record_publish)
    try:
        service.start(
            created["id"],
            VersionedCommand(
                command_id="start-one-done",
                expected_version=created["version"],
            ),
        )
        assert published_discussion_ids == [created["id"]]
        await asyncio.wait_for(sessions.bad_started.wait(), timeout=1)
        await asyncio.wait_for(sessions.good_finished.wait(), timeout=1)

        async def wait_for_terminal_wakeup() -> None:
            """等待 good 持久终态触发第二次状态唤醒。"""

            while len(published_discussion_ids) < 2:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait_for_terminal_wakeup(), timeout=1)
        snapshot = service.get(created["id"])
        status_by_name = {
            item["name"]: item["status"]
            for item in snapshot["rounds"][0]["participants"]
        }
        assert snapshot["rounds"][0]["status"] == "running"
        assert snapshot["rounds"][0]["terminal_participants"] == 1
        assert status_by_name == {"bad": "running", "good": "sealed"}
        assert snapshot["rounds"][0]["participants"][1]["completed_at"] is not None
        with opener() as repository:
            events = repository.list_events(created["id"], after_sequence=0)
        assert events[-1]["type"] == "participant_completed"
    finally:
        sessions.bad_release.set()
        await coordinator.stop()


@pytest.mark.anyio
async def test_success_terminal_without_trailing_text_becomes_empty_answer_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """空最终回答的实时终态和持久终态必须都收敛为失败。"""

    sessions = NoTrailingFinalTextSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    live_events: list[dict[str, Any]] = []
    original_publish = AttemptLiveEventPublisher.publish

    def record_live_event(
        publisher: AttemptLiveEventPublisher,
        event: dict[str, Any],
    ) -> None:
        """记录 bad attempt 的外发事件，同时保留真实 publisher 行为。"""

        session_id = event.get("session_id")
        if isinstance(session_id, str) and sessions.names.get(session_id) == "bad":
            live_events.append(event)
        original_publish(publisher, event)

    monkeypatch.setattr(AttemptLiveEventPublisher, "publish", record_live_event)
    created = await service.create(_request("no-final-text"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)

    bad = next(
        item
        for item in service.get(created["id"])["rounds"][0]["participants"]
        if item["name"] == "bad"
    )
    assert bad["status"] == "failed"
    assert bad["error_code"] == "EMPTY_ANSWER"
    assert bad["content"] is None
    assert [event["type"] for event in live_events][-2:] == ["finished", "error"]
    assert live_events[-1]["payload"]["subclass"] == "EMPTY_ANSWER"


@pytest.mark.anyio
async def test_runtime_disconnect_after_terminal_keeps_completed_answer(
    tmp_path: Path,
) -> None:
    """根终态已成立后，drain 断链不能覆盖成功结果或丢掉回答。"""

    service, coordinator, _, _ = _system(
        tmp_path,
        PostTerminalRuntimeErrorSessions(),
    )
    created = await service.create(_request("post-terminal-runtime-error"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)

    bad = next(
        item
        for item in service.get(created["id"])["rounds"][0]["participants"]
        if item["name"] == "bad"
    )
    assert bad["status"] == "succeeded"
    assert bad["content"] == "已经形成的完整回答"
    assert bad["error_code"] is None


@pytest.mark.anyio
async def test_codex_approval_is_declined_internally_without_timeout(
    tmp_path: Path,
) -> None:
    """私有 Codex 审批没有公开回答入口，必须立即拒绝而非等待十分钟。"""

    sessions = ApprovingSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("approval"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )

    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)
    published = service.get(created["id"])

    assert len(sessions.declined) == 1
    assert published["rounds"][0]["participants"][1]["content"] == (
        "审批被拒绝后仍可给出只读分析"
    )


@pytest.mark.anyio
async def test_runtime_usage_is_normalized_before_result_persistence(
    tmp_path: Path,
) -> None:
    """实时零值必须由原生历史纠正后再持久化为右栏统一结构。"""

    service, coordinator, _, _ = _system(tmp_path, UsageReportingSessions())
    created = await service.create(_request("normalized-usage"))
    service.start(
        created["id"],
        VersionedCommand(
            command_id="start-normalized-usage",
            expected_version=created["version"],
        ),
    )

    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)
    participants = {
        participant["name"]: participant
        for participant in service.get(created["id"])["rounds"][0]["participants"]
    }

    assert participants["bad"]["usage"] == {
        "input_tokens": 11086,
        "output_tokens": 3186,
        "cache_read_input_tokens": 23104,
        "cache_creation_input_tokens": 0,
        "total_tokens": 37376,
    }
    assert participants["good"]["usage"] == {
        "input_tokens": 15791,
        "output_tokens": 916,
        "cache_read_input_tokens": 12416,
        "reasoning_output_tokens": 512,
        "total_tokens": 16707,
    }


@pytest.mark.anyio
async def test_mixed_terminal_failures_keep_slots_and_do_not_block_publication(
    tmp_path: Path,
) -> None:
    """限额、runtime 超时和普通错误都保留明确槽位并与成功结果共同公开。"""

    sessions = MixedTerminalSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_mixed_request())
    service.start(
        created["id"],
        VersionedCommand(command_id="start-mixed", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)

    published = service.get(created["id"])
    statuses = {
        slot["name"]: slot["status"] for slot in published["rounds"][0]["participants"]
    }
    assert published["rounds"][0]["status"] == "published"
    assert statuses == {
        "success": "succeeded",
        "limited": "limited",
        "runtime-timeout": "timed_out",
        "failed": "failed",
    }


@pytest.mark.anyio
async def test_next_round_keeps_failed_slots_as_inline_statuses(
    tmp_path: Path,
) -> None:
    """失败参与者没有正文文件，下一轮仍按原位置给出真实终态。"""

    sessions = MixedTerminalSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    request = _mixed_request().model_copy(
        update={"progression_mode": "automatic", "max_rounds": 2}
    )
    created = await service.create(request)
    service.start(
        created["id"],
        VersionedCommand(
            command_id="start-mixed-paths",
            expected_version=created["version"],
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)

    second_prompts = [items[1] for items in sessions.prompts.values()]

    assert len(set(second_prompts)) == 1
    prompt_lines = second_prompts[0].splitlines()
    assert any(
        line.startswith("success：/") and line.endswith("final.md")
        for line in prompt_lines
    )
    assert any(line.startswith("limited：[limited]") for line in prompt_lines)
    assert any(
        line.startswith("runtime-timeout：[timed_out]") for line in prompt_lines
    )
    assert any(line.startswith("failed：[failed]") for line in prompt_lines)


@pytest.mark.anyio
async def test_restart_creates_new_attempt_for_same_round_and_hides_old_partial(
    tmp_path: Path,
) -> None:
    hanging = HangingSessions()
    first_service, first_coordinator, opener, artifacts = _system(tmp_path, hanging)
    created = await first_service.create(_request("restart"))
    first_service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(hanging.bad_started.wait(), timeout=1)
    while True:
        with opener() as repository:
            before_crash = repository.get_discussion(created["id"])
        status_by_name = {
            participant.name: next(
                result.status
                for result in before_crash.rounds[0].results
                if result.participant_id == participant.id
            )
            for participant in before_crash.participants
        }
        if status_by_name["good"] == "succeeded":
            break
        await asyncio.sleep(0)

    await first_coordinator.stop()
    with opener() as repository:
        interrupted = repository.get_discussion(created["id"])
    assert interrupted.status == "needs_reconcile"
    assert len(interrupted.rounds) == 1

    resumed_sessions = FakeParticipantSessions()
    second_events = DiscussionEventBus()
    second_coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        resumed_sessions,
        second_events,
        DiscussionEpisodeWriter(
            artifacts,
            memory_root=tmp_path / "memory",
        ),
    )
    second_service = DiscussionService(
        opener,
        artifacts,
        second_coordinator,
        second_events,
        FakeConfigurationCatalog(),
    )
    await second_coordinator.start()
    await asyncio.wait_for(second_coordinator.wait_idle(created["id"]), timeout=2)

    recovered = second_service.get(created["id"])
    with opener() as repository:
        attempt_counts = repository.connection.execute(
            """
            SELECT p.name, COUNT(*) AS attempts
            FROM discussion_attempts a
            JOIN discussion_participants p ON p.id=a.participant_id
            GROUP BY p.id, p.name ORDER BY p.name
            """
        ).fetchall()

    assert len(recovered["rounds"]) == 1
    assert recovered["rounds"][0]["status"] == "published"
    assert {row["name"]: row["attempts"] for row in attempt_counts} == {
        "bad": 2,
        "good": 1,
    }
    assert "old-partial-must-never-publish" not in str(recovered)
    assert all(
        slot["status"] == "succeeded" for slot in recovered["rounds"][0]["participants"]
    )


@pytest.mark.anyio
async def test_publication_file_failure_becomes_reconcile_then_reuses_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """回答已封口但发布文件失败时，不公开半轮，也不重跑成功 participant。"""

    sessions = FakeParticipantSessions()
    service, coordinator, opener, artifacts = _system(tmp_path, sessions)
    original_write = artifacts.write_publication
    failures = 0

    def fail_once(*args: Any, **kwargs: Any):
        """第一次模拟 publication fsync 前失败，之后恢复正常。"""

        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("injected publication failure")
        return original_write(*args, **kwargs)

    monkeypatch.setattr(artifacts, "write_publication", fail_once)
    created = await service.create(_request("publication-failure"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    with pytest.raises(OSError, match="injected publication failure"):
        await coordinator.wait_idle(created["id"])
    await asyncio.sleep(0)

    pending = service.get(created["id"])
    assert pending["status"] == "needs_reconcile"
    assert pending["rounds"][0]["status"] == "running"
    assert all(slot["content"] is None for slot in pending["rounds"][0]["participants"])
    with opener() as repository:
        attempts_before = repository.connection.execute(
            "SELECT COUNT(*) AS total FROM discussion_attempts"
        ).fetchone()["total"]

    resumed = service.resume(
        created["id"],
        VersionedCommand(
            command_id="resume-publication",
            expected_version=pending["version"],
        ),
    )
    assert resumed["status"] == "running"
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)

    published = service.get(created["id"])
    with opener() as repository:
        attempts_after = repository.connection.execute(
            "SELECT COUNT(*) AS total FROM discussion_attempts"
        ).fetchone()["total"]
    assert published["rounds"][0]["status"] == "published"
    assert attempts_after == attempts_before == 2


@pytest.mark.anyio
async def test_restart_after_pre_round_provision_failure_returns_to_draft(
    tmp_path: Path,
) -> None:
    """首轮前创建会话失败只需补齐 participant，不能伪造 running 空轮。"""

    sessions = ProvisionFailsOnceSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("provision-before-round"))

    assert created["status"] == "needs_reconcile"
    assert created["active_round_number"] is None
    assert created["rounds"] == []

    await coordinator.start()
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    recovered = service.get(created["id"])

    assert recovered["status"] == "draft"
    assert recovered["active_round_number"] is None
    assert recovered["rounds"] == []


@pytest.mark.anyio
async def test_explicit_resume_after_pre_round_provision_failure_returns_to_draft(
    tmp_path: Path,
) -> None:
    """用户显式 resume 与启动恢复共用首轮前补偿，但不能提前伪造 running。"""

    sessions = ProvisionFailsOnceSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("explicit-pre-round-resume"))

    response = service.resume(
        created["id"],
        VersionedCommand(
            command_id="resume-provision",
            expected_version=created["version"],
        ),
    )
    assert response["status"] == "needs_reconcile"
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)

    recovered = service.get(created["id"])
    assert recovered["status"] == "draft"
    assert recovered["active_round_number"] is None
    assert recovered["rounds"] == []

    retried = service.resume(
        created["id"],
        VersionedCommand(
            command_id="resume-provision",
            expected_version=created["version"],
        ),
    )
    assert retried["status"] == "draft"


@pytest.mark.anyio
async def test_restart_after_published_automatic_round_creates_next_round(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """发布事务后 transcript 故障不能把 automatic 永久卡在已发布 active round。"""

    sessions = FakeParticipantSessions()
    service, coordinator, _, artifacts = _system(tmp_path, sessions)
    original_rebuild = artifacts.rebuild_transcript
    failures = 0

    def fail_once(*args: Any, **kwargs: Any):
        """第一次 publication 后重建失败，恢复时成功。"""

        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("injected post-publication failure")
        return original_rebuild(*args, **kwargs)

    monkeypatch.setattr(artifacts, "rebuild_transcript", fail_once)
    request = _request("automatic-post-publication").model_copy(
        update={"progression_mode": "automatic", "max_rounds": 2}
    )
    created = await service.create(request)
    service.start(
        created["id"],
        VersionedCommand(command_id="start-auto", expected_version=created["version"]),
    )
    with pytest.raises(OSError, match="post-publication"):
        await coordinator.wait_idle(created["id"])
    await asyncio.sleep(0)

    pending = service.get(created["id"])
    assert pending["status"] == "needs_reconcile"
    assert pending["rounds"][0]["status"] == "published"

    service.resume(
        created["id"],
        VersionedCommand(
            command_id="resume-auto",
            expected_version=pending["version"],
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    recovered = service.get(created["id"])

    assert recovered["status"] == "waiting_user"
    assert [item["status"] for item in recovered["rounds"]] == [
        "published",
        "published",
    ]


@pytest.mark.anyio
async def test_automatic_next_prompt_integrity_failure_records_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自动模式读取已发布清单构造下一轮时，漂移必须进入 durable 对账。"""

    service, coordinator, opener, artifacts = _system(
        tmp_path,
        FakeParticipantSessions(),
    )
    original_verify = artifacts.verify_publication
    calls = 0

    def fail_automatic_prompt(*args: Any, **kwargs: Any) -> None:
        """允许 transcript 首次验真，只破坏随后自动下一轮的 prompt 验真。"""

        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected automatic prompt artifact failure")
        original_verify(*args, **kwargs)

    monkeypatch.setattr(artifacts, "verify_publication", fail_automatic_prompt)
    request = _request("automatic-prompt-integrity").model_copy(
        update={"progression_mode": "automatic", "max_rounds": 2}
    )
    created = await service.create(request)
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )

    with pytest.raises(OSError, match="automatic prompt artifact"):
        await coordinator.wait_idle(created["id"])
    await asyncio.sleep(0)

    with opener() as repository:
        discussion = repository.get_discussion(created["id"])
        issue = repository.connection.execute(
            "SELECT issue_code FROM discussion_integrity_issues WHERE discussion_id=?",
            (created["id"],),
        ).fetchone()
    assert discussion.status == "needs_reconcile"
    assert len(discussion.rounds) == 1
    assert issue["issue_code"] == "ARTIFACT_INTEGRITY"


@pytest.mark.anyio
async def test_local_artifact_failure_uses_reconcile_not_fake_host_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地 input/hash 故障必须抛给协调器对账，不能当作 runtime 明确断联发布。"""

    sessions = FakeParticipantSessions()
    service, coordinator, opener, artifacts = _system(tmp_path, sessions)
    created = await service.create(_request("local-artifact-failure"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start-local", expected_version=created["version"]),
    )
    original_read = artifacts.read_text

    def fail_round_input(
        relative_path: str,
        *,
        expected_sha256: str | None = None,
        expected_bytes: int | None = None,
    ):
        """只破坏 participant 即将读取的 content-addressed round input。"""

        del expected_bytes
        if "/inputs/" in relative_path:
            raise ValueError("injected input hash mismatch")
        return original_read(relative_path, expected_sha256=expected_sha256)

    monkeypatch.setattr(artifacts, "read_text", fail_round_input)
    with pytest.raises(ExceptionGroup, match="TaskGroup") as captured:
        await coordinator.wait_idle(created["id"])
    assert all(
        isinstance(item, ValueError) and "input hash mismatch" in str(item)
        for item in captured.value.exceptions
    )
    await asyncio.sleep(0)

    pending = service.get(created["id"])
    with opener() as repository:
        attempt_statuses = {
            str(row["status"])
            for row in repository.connection.execute(
                "SELECT status FROM discussion_attempts"
            ).fetchall()
        }
        integrity_issue = repository.connection.execute(
            "SELECT issue_code FROM discussion_integrity_issues WHERE discussion_id=?",
            (created["id"],),
        ).fetchone()
    assert pending["status"] == "needs_reconcile"
    assert pending["rounds"][0]["status"] == "running"
    assert "host_lost" not in attempt_statuses
    assert attempt_statuses == {"needs_reconcile"}
    assert integrity_issue["issue_code"] == "ARTIFACT_INTEGRITY"


@pytest.mark.anyio
async def test_one_worker_failure_cancels_and_awaits_hanging_sibling(
    tmp_path: Path,
) -> None:
    """局部异常返回前必须收掉同轮其他 worker，恢复时不能重叠旧 turn。"""

    sessions = FailAndHangSessions()
    service, coordinator, _, _ = _system(tmp_path, sessions)
    created = await service.create(_request("worker-structured-concurrency"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )

    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        await coordinator.wait_idle(created["id"])
    await asyncio.wait_for(sessions.hanging_exited.wait(), timeout=1)
    await asyncio.sleep(0)

    pending = service.get(created["id"])
    assert pending["status"] == "needs_reconcile"
    assert coordinator._active_sessions.get(created["id"], set()) == set()
    assert sessions.interrupted_sessions

    service.resume(
        created["id"],
        VersionedCommand(
            command_id="resume",
            expected_version=pending["version"],
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    assert service.get(created["id"])["rounds"][0]["status"] == "published"


@pytest.mark.anyio
async def test_startup_rebuilds_transcript_after_user_guided_publish_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """waiting_user 已是公开事实时，重启仍补齐发布事务后的派生 transcript。"""

    sessions = FakeParticipantSessions()
    service, coordinator, opener, artifacts = _system(tmp_path, sessions)
    original_rebuild = artifacts.rebuild_transcript

    def fail_rebuild(*args: Any, **kwargs: Any):
        """模拟 user-guided publication 提交后的进程崩溃。"""

        del args, kwargs
        raise OSError("injected waiting-user transcript failure")

    monkeypatch.setattr(artifacts, "rebuild_transcript", fail_rebuild)
    created = await service.create(_request("waiting-user-transcript"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    with pytest.raises(OSError, match="waiting-user transcript"):
        await coordinator.wait_idle(created["id"])
    assert service.get(created["id"])["status"] == "waiting_user"

    monkeypatch.setattr(artifacts, "rebuild_transcript", original_rebuild)
    restarted = DiscussionCoordinator(
        opener,
        artifacts,
        FakeParticipantSessions(),
        DiscussionEventBus(),
        DiscussionEpisodeWriter(artifacts, memory_root=tmp_path / "memory"),
    )
    await restarted.start()
    transcript = artifacts.read_text(f"discussions/{created['id']}/transcript.md")

    assert "answer-bad-round-1" in transcript
    assert "answer-good-round-1" in transcript


@pytest.mark.anyio
async def test_continue_records_integrity_outbox_when_publication_bytes_drift(
    tmp_path: Path,
) -> None:
    """用户模式建下一轮时发现发布清单漂移，必须留下 durable 对账入口。"""

    service, coordinator, opener, _ = _system(tmp_path, FakeParticipantSessions())
    created = await service.create(_request("continue-integrity"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    waiting = service.get(created["id"])
    with opener() as repository, repository.transaction():
        repository.connection.execute(
            """
            UPDATE discussion_rounds SET publication_bytes=publication_bytes + 1
            WHERE discussion_id=? AND number=1
            """,
            (created["id"],),
        )

    with pytest.raises(ValueError, match="byte count mismatch"):
        service.continue_round(
            created["id"],
                ContinueDiscussionRequest(
                    command_id="continue",
                    expected_version=waiting["version"],
                ),
        )

    with opener() as repository:
        issue = repository.connection.execute(
            "SELECT issue_code FROM discussion_integrity_issues WHERE discussion_id=?",
            (created["id"],),
        ).fetchone()
    assert issue["issue_code"] == "ARTIFACT_INTEGRITY"


@pytest.mark.anyio
async def test_continue_rejects_tampered_previous_output_and_records_integrity_issue(
    tmp_path: Path,
) -> None:
    """上一轮正文被同字节篡改后，不能把未验真的路径交给下一轮。"""

    service, coordinator, opener, artifacts = _system(
        tmp_path,
        FakeParticipantSessions(),
    )
    created = await service.create(_request("tampered-previous-output"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    waiting = service.get(created["id"])
    with opener() as repository:
        discussion = repository.get_discussion(created["id"])
    output_artifact = discussion.rounds[0].results[0].output_artifact
    assert output_artifact is not None
    output_path = artifacts.data_root / output_artifact
    original = output_path.read_bytes()
    assert original
    replacement = b"X" if original[:1] != b"X" else b"Y"
    output_path.write_bytes(replacement + original[1:])

    with pytest.raises(ValueError, match="hash mismatch"):
        service.continue_round(
            created["id"],
            ContinueDiscussionRequest(
                command_id="continue-after-output-tamper",
                expected_version=waiting["version"],
            ),
        )

    with opener() as repository:
        issue = repository.connection.execute(
            "SELECT issue_code FROM discussion_integrity_issues WHERE discussion_id=?",
            (created["id"],),
        ).fetchone()
    assert issue["issue_code"] == "ARTIFACT_INTEGRITY"


@pytest.mark.anyio
async def test_get_rejects_missing_publication_manifest_and_records_outbox(
    tmp_path: Path,
) -> None:
    """published 清单缺失时不能只靠槽位文件继续拼公开 DTO。"""

    service, coordinator, opener, artifacts = _system(
        tmp_path,
        FakeParticipantSessions(),
    )
    created = await service.create(_request("missing-publication"))
    service.start(
        created["id"],
        VersionedCommand(command_id="start", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    with opener() as repository:
        discussion = repository.get_discussion(created["id"])
    publication = discussion.rounds[0].publication_artifact
    assert publication is not None
    (artifacts.data_root / publication).unlink()

    with pytest.raises(FileNotFoundError):
        service.get(created["id"])

    with opener() as repository:
        issue = repository.connection.execute(
            "SELECT issue_code FROM discussion_integrity_issues WHERE discussion_id=?",
            (created["id"],),
        ).fetchone()
    assert issue["issue_code"] == "ARTIFACT_INTEGRITY"
