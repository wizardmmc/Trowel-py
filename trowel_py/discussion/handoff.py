"""把已公开研讨事实组装成普通 Agent 可继续执行的可追溯现场。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

from trowel_py.agent_host.binding import SessionBinding
from trowel_py.agent_host.hub import SessionHub, SessionHubError
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.errors import DiscussionRuntimeError
from trowel_py.discussion.models import (
    Discussion,
    DiscussionParticipant,
    ParticipantResult,
)

_TOPIC_LIMIT = 4_000
_SUPPLEMENTS_LIMIT = 6_000
_STANCE_LIMIT = 2_000
_MARKS_LIMIT = 6_000


@dataclass(frozen=True)
class HandoffSessionResult:
    """记录普通 Agent 已接受交接现场后的公开身份。

    Attributes:
        agent_session_id: 新建普通 Agent 的 Trowel 会话 ID。
        turn_id: 已接受交接现场的首轮 ID。
    """

    agent_session_id: str
    turn_id: str


class HandoffSessionPort(Protocol):
    """约束 discussion 创建普通 Agent 并发送交接现场的操作。"""

    async def create_and_start(
        self,
        *,
        request_id: str,
        request: CreateAgentSessionRequest,
        prompt: str,
        instruction: str,
    ) -> HandoffSessionResult:
        """幂等创建普通 Agent，并发送用户明确给出的首条指令。"""
        ...


class AgentHostHandoffSessionAdapter:
    """通过应用唯一 Session Hub 创建并激活普通 Agent 会话。"""

    def __init__(self, hub: SessionHub) -> None:
        """保存会话创建和首轮发送使用的 Session Hub。

        Args:
            hub: 当前应用唯一的 Agent 会话边界。
        """

        self._hub = hub

    async def create_and_start(
        self,
        *,
        request_id: str,
        request: CreateAgentSessionRequest,
        prompt: str,
        instruction: str,
    ) -> HandoffSessionResult:
        """按稳定请求 ID 合并重试，并避免重复发送已接受的首轮。

        Args:
            request_id: discussion handoff 命令派生的稳定创建身份。
            request: 普通 Agent 的运行条件和上下文开关。
            prompt: 后端从已公开事实确定性组装的系统级交接现场。
            instruction: 用户在交接弹窗中输入的首条原话。

        Returns:
            新会话和已经接受的首轮 ID。

        Raises:
            DiscussionRuntimeError: 会话创建或首轮接受结果无法确认。
        """

        logical_turn_id = (
            _handoff_turn_id(request_id)
            if request.runtime == "claude_code"
            else None
        )
        resumed_accepted_cc_turn = False

        async def create_once() -> SessionBinding:
            """执行一次完整的普通 Agent 会话创建。"""

            return await self._hub.create_complete_session(
                request,
                bootstrap_context=prompt,
            )

        try:
            binding = (
                self._hub.store.find_by_owner_ref(request.owner_ref)
                if request.owner_ref is not None
                else None
            )
            if binding is not None:
                if _is_live(self._hub, binding.session_id):
                    recovered_turn_id = await _accepted_turn_id(
                        self._hub,
                        binding.session_id,
                        fallback_turn_id=logical_turn_id,
                    )
                    if recovered_turn_id is not None:
                        self._hub.activate(binding.session_id)
                        return HandoffSessionResult(
                            binding.session_id, recovered_turn_id
                        )
                else:
                    native_session_id = binding.native_session_id
                    resumed_accepted_cc_turn = (
                        request.runtime == "claude_code"
                        and native_session_id is not None
                    )
                    await self._hub.delete(binding.session_id)
                    request = request.model_copy(
                        update={"resume_from": native_session_id}
                    )
                    request_id = f"{request_id}:recovery:{native_session_id or 'unstarted'}"
                    binding = None
            if binding is None:
                fingerprint = hashlib.sha256(
                    (
                        request.model_dump_json()
                        + "\n"
                        + prompt
                        + "\n"
                        + instruction
                    ).encode("utf-8")
                ).hexdigest()
                binding = await self._hub.coalesce_session_create(
                    request_id,
                    fingerprint,
                    create_once,
                )
            existing_turn_id = await _accepted_turn_id(
                self._hub,
                binding.session_id,
                fallback_turn_id=logical_turn_id,
                assume_fallback_accepted=resumed_accepted_cc_turn,
            )
            turn_id = existing_turn_id or await self._hub.start_turn(
                binding.session_id,
                instruction,
                autonomous=False,
                memory_eligible=False,
                reserved_turn_id=logical_turn_id,
            )
            self._hub.activate(binding.session_id)
        except SessionHubError as exc:
            raise DiscussionRuntimeError(
                "普通 Agent 的交接会话创建或首轮接受结果未知"
            ) from exc
        return HandoffSessionResult(binding.session_id, turn_id)


def _current_turn_id(hub: SessionHub, session_id: str) -> str | None:
    """从公开活动快照读取同一请求重试时已经接受的首轮 ID。"""

    sessions, _active_id = hub.list_active()
    for item in sessions:
        if item.get("session_id") == session_id:
            turn_id = item.get("current_turn_id")
            return turn_id if isinstance(turn_id, str) and turn_id else None
    return None


def _is_live(hub: SessionHub, session_id: str) -> bool:
    """判断 owner binding 在当前 Session Hub 中是否仍有 runtime 实例。"""

    sessions, _active_id = hub.list_active()
    return any(
        item.get("session_id") == session_id
        and item.get("connected") is not False
        for item in sessions
    )


async def _accepted_turn_id(
    hub: SessionHub,
    session_id: str,
    *,
    fallback_turn_id: str | None = None,
    assume_fallback_accepted: bool = False,
) -> str | None:
    """从实时状态或原生历史恢复已经接受的交接首轮。

    Codex 历史携带原生 turn ID，可以直接对账。Claude Code 历史没有稳定的
    turn ID，因此首次发送前使用命令身份派生逻辑 ID；恢复时只要已有原生会话
    身份，就说明首次输入至少已被 Claude Code 接受。
    """

    current = _current_turn_id(hub, session_id)
    if current is not None:
        return current
    history = await hub.history(session_id)
    for event in history:
        turn_id = event.get("turn_id")
        if isinstance(turn_id, str) and turn_id:
            return turn_id
    if fallback_turn_id is not None and (history or assume_fallback_accepted):
        return fallback_turn_id
    return None


def _handoff_turn_id(request_id: str) -> str:
    """从稳定 handoff 命令身份派生 Claude Code 的逻辑首轮 ID。"""

    return hashlib.sha256(f"{request_id}:bootstrap-turn".encode("utf-8")).hexdigest()[
        :32
    ]


def build_handoff_prompt(
    discussion: Discussion,
    artifacts: DiscussionArtifactStore,
    marked_sources: frozenset[str],
    transcript_access_token: str | None = None,
) -> str:
    """从公开轮次、用户原话和标记生成有界交接现场。

    完整记录始终通过本地只读路径保留。正文截断只影响内联预览，不生成总结，
    也不会把未发布槽位带入新 Agent。

    Args:
        discussion: 当前完整领域聚合。
        artifacts: 用于验真公开正文并重建 transcript 的存储。
        marked_sources: ``round:<n>:participant:<id>`` 形式的用户标记来源。
        transcript_access_token: 桌面包中只允许 GET 当前记录的路径级令牌。

    Returns:
        可以直接作为普通 Agent 首轮输入的确定性文本。
    """

    artifacts.rebuild_transcript(discussion)
    port = os.environ.get("TROWEL_SERVER_PORT", "8000")
    transcript_path = transcript_api_path(discussion.id)
    transcript_url = f"http://127.0.0.1:{port}{transcript_path}"
    if transcript_access_token:
        transcript_url += (
            f"?access_token={quote(transcript_access_token, safe='')}"
        )
    participant_by_id = {item.id: item for item in discussion.participants}
    published_rounds = [
        item for item in discussion.rounds if item.status == "published"
    ]
    last_results: dict[str, tuple[int, ParticipantResult]] = {}
    marked_lines: list[str] = []
    for round_record in published_rounds:
        for result in round_record.results:
            last_results[result.participant_id] = (round_record.number, result)
            source_ref = _mark_source(round_record.number, result.participant_id)
            if source_ref in marked_sources:
                name = participant_by_id[result.participant_id].name
                marked_lines.append(
                    f"- 第 {round_record.number} 轮 · {name}："
                    f"{_result_text(result, artifacts, _STANCE_LIMIT)}"
                )
    supplements = [
        f"- {_message_target(message.target_participant_id, participant_by_id)}："
        f"{artifacts.read_user_message_body(message)}"
        for message in discussion.messages[1:]
    ]
    stance_lines = []
    for participant in discussion.participants:
        latest = last_results.get(participant.id)
        if latest is None:
            stance_lines.append(f"- {participant.name}：尚无已公开发言")
            continue
        round_number, result = latest
        stance_lines.append(
            f"- {participant.name}（第 {round_number} 轮）："
            f"{_result_text(result, artifacts, _STANCE_LIMIT)}"
        )
    stop_reason = next(
        (
            item.stop_reason
            for item in reversed(discussion.rounds)
            if item.stop_reason
        ),
        None,
    )
    unresolved = [
        participant_by_id[result.participant_id].name
        for round_record in discussion.rounds
        if round_record.status != "published"
        for result in round_record.results
        if result.status not in {"succeeded", "failed", "limited", "timed_out", "interrupted", "cancelled", "host_lost"}
    ]
    sections = [
        "# Trowel 研讨交接",
        "",
        f"来源研讨：{discussion.id}",
        f"当前状态：{discussion.status}",
        f"停止原因：{stop_reason or '无'}",
        f"尚未完成：{('、'.join(dict.fromkeys(unresolved)) if unresolved else '无')}",
        "",
        "## 原始问题",
        "",
        _clip(discussion.topic, _TOPIC_LIMIT),
        "",
        "## 用户后续补充",
        "",
        _clip("\n".join(supplements) if supplements else "无", _SUPPLEMENTS_LIMIT),
        "",
        "## 各方最后公开立场",
        "",
        "\n".join(stance_lines) if stance_lines else "无已公开轮次",
        "",
        "## 用户标记",
        "",
        _clip("\n".join(marked_lines) if marked_lines else "无", _MARKS_LIMIT),
        "",
        "## 完整记录",
        "",
        f"应用只读链接：{transcript_url}",
        "需要核对原文、失败原因或更早轮次时，通过 GET 读取该链接。",
        "",
        "请基于以上现场继续处理原问题；保留分歧，并在需要时回到完整记录核对证据。",
    ]
    return "\n".join(sections).rstrip() + "\n"


def transcript_api_path(discussion_id: str) -> str:
    """返回一个 discussion 完整公开记录的规范只读路径。"""

    return f"/api/discussions/{quote(discussion_id, safe='')}/transcript"


def _result_text(
    result: ParticipantResult,
    artifacts: DiscussionArtifactStore,
    limit: int,
) -> str:
    """读取一个已公开槽位的成功正文或真实失败原因。"""

    if result.status == "succeeded" and result.output_artifact:
        return _clip(
            artifacts.read_text(
                result.output_artifact,
                expected_sha256=result.output_sha256,
                expected_bytes=result.output_bytes,
            ),
            limit,
        )
    return f"[{result.status}] {result.error_message or result.error_code or '无更多信息'}"


def _message_target(
    target_id: str | None, participants: dict[str, DiscussionParticipant]
) -> str:
    """把顶层用户消息的目标 ID 转成可读名称。"""

    if target_id is None:
        return "全体"
    participant = participants.get(target_id)
    return participant.name if participant is not None else "未知参与者"


def _clip(text: str, limit: int) -> str:
    """按字符数稳定截断内联现场，并明确提示回读完整记录。"""

    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "\n[内联内容已截断，请读取完整记录]"


def _mark_source(round_number: int, participant_id: str) -> str:
    """生成用户标记在数据库中的稳定来源引用。"""

    return f"round:{round_number}:participant:{participant_id}"
