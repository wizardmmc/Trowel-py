"""保存研讨正文 artifact，并把所有数据库引用限制为数据根内相对路径。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trowel_py.application_paths import resolve_application_data_root
from trowel_py.discussion.models import Discussion, DiscussionRound, UserMessage


@dataclass(frozen=True)
class ArtifactRef:
    """记录一个已经 durable 的 artifact 引用及完整性信息。

    Attributes:
        relative_path: 相对于应用数据根的可迁移路径。
        sha256: 文件原始字节的 SHA-256。
        byte_count: 文件原始字节数。
    """

    relative_path: str
    sha256: str
    byte_count: int


class DiscussionArtifactStore:
    """以文件先行协议读写 discussion 私有正文和派生 transcript。

    Attributes:
        data_root: Trowel 当前应用数据根。
    """

    def __init__(self, data_root: Path | None = None) -> None:
        """创建只允许访问 ``data_root/discussions`` 的 artifact store。

        Args:
            data_root: 测试可注入的数据根；正式运行省略。
        """

        self.data_root = (
            data_root.resolve()
            if data_root is not None
            else resolve_application_data_root().resolve()
        )
        self._discussion_root = self.data_root / "discussions"

    def write_user_message(
        self,
        *,
        discussion_id: str,
        message_id: str,
        sequence: int,
        after_round_number: int,
        target_scope: str,
        target_participant_id: str | None,
        body: str,
    ) -> ArtifactRef:
        """写入一条 Profile 可独立验真的顶层用户消息 JSON。

        Args:
            discussion_id: 所属研讨 ID。
            message_id: 消息稳定 ID。
            sequence: 研讨内用户消息顺序。
            after_round_number: 消息位于哪轮公开之后。
            target_scope: all 或 participant。
            target_participant_id: 单方消息的目标 ID。
            body: 用户原话。

        Returns:
            已 durable 的相对路径、hash 和字节数。
        """

        payload = {
            "schema": "trowel.discussion.user-message.v1",
            "discussion_id": discussion_id,
            "message_id": message_id,
            "sequence": sequence,
            "after_round_number": after_round_number,
            "author_role": "user",
            "target_scope": target_scope,
            "target_participant_id": target_participant_id,
            "content": body,
        }
        body_bytes = self._json_bytes(payload)
        digest = hashlib.sha256(body_bytes).hexdigest()
        relative = (
            Path("discussions")
            / discussion_id
            / "messages"
            / (f"{sequence:06d}-{message_id}-{digest}.json")
        )
        return self._write_bytes(
            relative,
            body_bytes,
            immutable=True,
        )

    def write_round_input(
        self,
        *,
        discussion_id: str,
        round_number: int,
        kind: str,
        prompt: str,
    ) -> ArtifactRef:
        """写入一轮实际发送给所有 participant 的共同 prompt。

        Args:
            discussion_id: 所属研讨 ID。
            round_number: 从 1 开始的逻辑轮号。
            kind: regular 或 final，用于隔离同轮互斥命令留下的 orphan。
            prompt: 已由确定性代码构造的完整普通用户提示词。

        Returns:
            已 durable 且由 kind 和正文 hash 唯一定位的输入引用。
        """

        if kind not in {"regular", "final"}:
            raise ValueError("unknown discussion round kind")
        body = prompt.encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        relative = (
            self._round_dir(discussion_id, round_number)
            / "inputs"
            / f"{kind}-{digest}.txt"
        )
        return self._write_bytes(relative, body, immutable=True)

    def read_user_message_body(self, message: UserMessage) -> str:
        """按 hash 校验用户消息 artifact，并核对 SQLite 索引副本没有漂移。

        Args:
            message: 带稳定消息身份、正文副本和 artifact 引用的领域记录。

        Returns:
            artifact 中经过身份与 author 校验的用户原话。

        Raises:
            ValueError: JSON、消息身份、author 或 SQLite 正文副本不一致。
        """

        raw = self.read_text(
            message.message_artifact,
            expected_sha256=message.message_sha256,
            expected_bytes=message.message_bytes,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "discussion user message artifact is invalid JSON"
            ) from exc
        content = payload.get("content") if isinstance(payload, dict) else None
        valid = (
            isinstance(payload, dict)
            and payload.get("schema") == "trowel.discussion.user-message.v1"
            and payload.get("discussion_id") == message.discussion_id
            and payload.get("message_id") == message.id
            and payload.get("author_role") == "user"
            and payload.get("sequence") == message.sequence
            and payload.get("after_round_number") == message.after_round_number
            and payload.get("target_scope") == message.target_scope
            and payload.get("target_participant_id") == message.target_participant_id
            and content == message.body
        )
        if not valid or not isinstance(content, str):
            raise ValueError("discussion user message artifact identity mismatch")
        return content

    def initialize_attempt_events(
        self,
        *,
        discussion_id: str,
        round_number: int,
        participant_id: str,
        ordinal: int,
        attempt_id: str,
    ) -> ArtifactRef:
        """在派发前创建空的去敏 attempt 事件文件。

        Args:
            discussion_id: 所属研讨 ID。
            round_number: 所属轮号。
            participant_id: 所属 participant ID。
            ordinal: 此 participant 在本轮的尝试序号。
            attempt_id: 尝试稳定 ID。

        Returns:
            空 events.jsonl 的 durable 引用。
        """

        relative = (
            self._attempt_dir(
                discussion_id,
                round_number,
                participant_id,
                ordinal,
                attempt_id,
            )
            / "events.jsonl"
        )
        return self._write_bytes(relative, b"", immutable=False)

    def write_attempt_events(
        self,
        *,
        discussion_id: str,
        round_number: int,
        participant_id: str,
        ordinal: int,
        attempt_id: str,
        events: tuple[dict[str, Any], ...],
    ) -> ArtifactRef:
        """原子替换 attempt 的去敏事件摘要，不保存工具参数或结果正文。

        Args:
            discussion_id: 所属研讨 ID。
            round_number: 所属轮号。
            participant_id: 所属 participant ID。
            ordinal: 尝试序号。
            attempt_id: 尝试稳定 ID。
            events: 只含 turn 身份、usage、终态和错误分类的事件。

        Returns:
            最终 events.jsonl 引用。
        """

        relative = (
            self._attempt_dir(
                discussion_id,
                round_number,
                participant_id,
                ordinal,
                attempt_id,
            )
            / "events.jsonl"
        )
        body = b"".join(self._json_bytes(event) + b"\n" for event in events)
        return self._write_bytes(relative, body, immutable=False)

    def write_attempt_output(
        self,
        *,
        discussion_id: str,
        round_number: int,
        participant_id: str,
        ordinal: int,
        attempt_id: str,
        text: str,
    ) -> ArtifactRef:
        """在 attempt 成功入库前 durable 写入完整可见回答。

        Args:
            discussion_id: 所属研讨 ID。
            round_number: 所属轮号。
            participant_id: 所属 participant ID。
            ordinal: 尝试序号。
            attempt_id: 尝试稳定 ID。
            text: 当前 attempt 从 text delta 组合出的完整回答。

        Returns:
            不可变 final.md 引用。
        """

        relative = (
            self._attempt_dir(
                discussion_id,
                round_number,
                participant_id,
                ordinal,
                attempt_id,
            )
            / "final.md"
        )
        return self._write_bytes(relative, text.encode("utf-8"), immutable=True)

    def write_publication(
        self,
        discussion: Discussion,
        round_record: DiscussionRound,
    ) -> ArtifactRef:
        """从完整终态槽位写出不含 session 身份的原子发布清单。

        Args:
            discussion: 当前研讨聚合，用于取得稳定 participant 名称和顺序。
            round_record: 尚未在 SQLite 标成 published 的终态轮。

        Returns:
            已 durable 的 publication.json 引用。
        """

        payload = self._publication_payload(discussion, round_record)
        relative = (
            self._round_dir(discussion.id, round_record.number) / "publication.json"
        )
        return self._write_bytes(relative, self._json_bytes(payload), immutable=True)

    def verify_publication(
        self,
        discussion: Discussion,
        round_record: DiscussionRound,
    ) -> None:
        """核对已发布清单的 hash、字节数、身份和 SQLite 完整快照。

        Args:
            discussion: 清单所属研讨聚合。
            round_record: status=published 且带发布引用的轮次。

        Raises:
            ValueError: 引用缺失或清单与 SQLite 公开事实不一致。
            OSError: 清单文件不存在或不可读。
        """

        if (
            round_record.publication_artifact is None
            or round_record.publication_sha256 is None
            or round_record.publication_bytes is None
        ):
            raise ValueError("discussion publication reference is incomplete")
        actual = self.read_json(
            round_record.publication_artifact,
            expected_sha256=round_record.publication_sha256,
            expected_bytes=round_record.publication_bytes,
        )
        if actual != self._publication_payload(discussion, round_record):
            raise ValueError("discussion publication manifest mismatch")

    def rebuild_transcript(self, discussion: Discussion) -> ArtifactRef:
        """只从 SQLite 已公开轮次重建供人阅读的累加 transcript。

        Args:
            discussion: 最新研讨聚合；未 published 的槽位全部忽略。

        Returns:
            可覆盖派生 transcript.md 的最新引用。
        """

        if not discussion.messages:
            raise ValueError("discussion initial user message is missing")
        topic = self.read_user_message_body(discussion.messages[0])
        if topic != discussion.topic:
            raise ValueError("discussion topic and initial message differ")
        lines = [f"# {topic}", "", "## 初始议题", "", topic]
        participant_by_id = {item.id: item for item in discussion.participants}
        messages_by_round: dict[int, list[UserMessage]] = {}
        for message in discussion.messages[1:]:
            messages_by_round.setdefault(message.after_round_number, []).append(message)
        for round_record in discussion.rounds:
            if round_record.status != "published":
                continue
            self.verify_publication(discussion, round_record)
            lines.extend(["", f"## 第 {round_record.number} 轮", ""])
            for result in round_record.results:
                participant = participant_by_id[result.participant_id]
                lines.append(f"### {participant.name}")
                lines.append("")
                if result.status == "succeeded" and result.output_artifact:
                    lines.append(
                        self.read_text(
                            result.output_artifact,
                            expected_sha256=result.output_sha256,
                            expected_bytes=result.output_bytes,
                        )
                    )
                else:
                    reason = result.error_message or result.status
                    lines.append(f"[{result.status}] {reason}")
                lines.append("")
            supplements = messages_by_round.get(round_record.number, [])
            if supplements:
                lines.extend(["### 用户补充", ""])
                for message in supplements:
                    target = (
                        "全体"
                        if message.target_scope == "all"
                        else participant_by_id[message.target_participant_id or ""].name
                    )
                    lines.append(f"- [{target}] {self.read_user_message_body(message)}")
        body = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        relative = Path("discussions") / discussion.id / "transcript.md"
        return self._write_bytes(relative, body, immutable=False)

    def verify_discussion(self, discussion: Discussion) -> None:
        """复核一场研讨全部公共输入、用户原话和已发布输出引用。

        Args:
            discussion: 要执行启动补偿或人工修复确认的完整聚合。
        """

        for message in discussion.messages:
            self.read_user_message_body(message)
        for round_record in discussion.rounds:
            self.read_text(
                round_record.input_artifact,
                expected_sha256=round_record.public_context_hash,
                expected_bytes=round_record.input_bytes,
            )
            if round_record.status != "published":
                continue
            self.verify_publication(discussion, round_record)
            for result in round_record.results:
                if result.status == "succeeded" and result.output_artifact:
                    self.read_text(
                        result.output_artifact,
                        expected_sha256=result.output_sha256,
                        expected_bytes=result.output_bytes,
                    )

    def read_text(
        self,
        relative_path: str,
        *,
        expected_sha256: str | None = None,
        expected_bytes: int | None = None,
    ) -> str:
        """读取并可选校验数据根内的 UTF-8 artifact。

        Args:
            relative_path: 数据库保存的相对路径。
            expected_sha256: 存在时要求字节 hash 完全一致。
            expected_bytes: 存在时要求原始字节数完全一致。

        Returns:
            解码后的 UTF-8 文本。

        Raises:
            ValueError: 路径越界或 hash 不匹配。
            OSError: 文件不存在或不可读。
            UnicodeDecodeError: artifact 不是 UTF-8。
        """

        path = self._resolve_relative(relative_path)
        body = path.read_bytes()
        if expected_bytes is not None and len(body) != expected_bytes:
            raise ValueError("discussion artifact byte count mismatch")
        if expected_sha256 is not None:
            actual = hashlib.sha256(body).hexdigest()
            if actual != expected_sha256:
                raise ValueError("discussion artifact hash mismatch")
        return body.decode("utf-8")

    def verified_path(
        self,
        relative_path: str,
        *,
        expected_sha256: str,
        expected_bytes: int | None = None,
    ) -> Path:
        """校验 artifact 完整性并返回数据根内的绝对路径。

        Args:
            relative_path: 数据库保存的相对路径。
            expected_sha256: 数据库保存的完整 SHA-256。
            expected_bytes: 数据库保存的原始字节数。

        Returns:
            已确认位于数据根内且内容 hash 匹配的绝对路径。
        """

        path = self._resolve_relative(relative_path)
        body = path.read_bytes()
        if expected_bytes is not None and len(body) != expected_bytes:
            raise ValueError("discussion artifact byte count mismatch")
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected_sha256:
            raise ValueError("discussion artifact hash mismatch")
        return path

    def read_json(
        self,
        relative_path: str,
        *,
        expected_sha256: str | None = None,
        expected_bytes: int | None = None,
    ) -> dict[str, Any]:
        """读取并校验一个 JSON object artifact。

        Args:
            relative_path: 数据库保存的相对路径。
            expected_sha256: 可选内容指纹。
            expected_bytes: 可选原始字节数。

        Returns:
            JSON 根对象。

        Raises:
            ValueError: 根节点不是对象或完整性校验失败。
        """

        value = json.loads(
            self.read_text(
                relative_path,
                expected_sha256=expected_sha256,
                expected_bytes=expected_bytes,
            )
        )
        if not isinstance(value, dict):
            raise ValueError("discussion JSON artifact must contain an object")
        return value

    @staticmethod
    def _publication_payload(
        discussion: Discussion,
        round_record: DiscussionRound,
    ) -> dict[str, Any]:
        """从 SQLite 聚合构造发布清单的唯一规范形态。

        Args:
            discussion: 提供参与者冻结名称和顺序的研讨。
            round_record: 提供公开槽位事实的轮次。

        Returns:
            可写入或与磁盘 JSON 严格比较的对象。
        """

        by_id = {item.id: item for item in discussion.participants}
        slots = []
        for result in round_record.results:
            participant = by_id[result.participant_id]
            slots.append(
                {
                    "participant_id": participant.id,
                    "position": participant.position,
                    "name": participant.name,
                    "status": result.status,
                    "output_artifact": result.output_artifact,
                    "output_sha256": result.output_sha256,
                    "output_bytes": result.output_bytes,
                    "error_code": result.error_code,
                    "error_message": result.error_message,
                    "usage": (
                        json.loads(result.usage_json) if result.usage_json else None
                    ),
                    "started_at": result.started_at,
                    "completed_at": result.completed_at,
                }
            )
        return {
            "schema": "trowel.discussion.publication.v1",
            "discussion_id": discussion.id,
            "round_id": round_record.id,
            "round_number": round_record.number,
            "kind": round_record.kind,
            "public_context_hash": round_record.public_context_hash,
            "slots": slots,
        }

    def _write_bytes(
        self,
        relative_path: Path,
        body: bytes,
        *,
        immutable: bool,
    ) -> ArtifactRef:
        """用同目录唯一临时文件、fsync 和 replace 写入私有 artifact。

        Args:
            relative_path: 必须位于 discussions 目录下的相对路径。
            body: 要写入的原始字节。
            immutable: 目标已存在时是否要求内容完全相同。

        Returns:
            最终文件引用。
        """

        target = self._resolve_relative(str(relative_path))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        expected_hash = hashlib.sha256(body).hexdigest()
        if immutable and target.exists():
            existing = target.read_bytes()
            if existing != body:
                raise FileExistsError("immutable discussion artifact already exists")
            return ArtifactRef(str(relative_path), expected_hash, len(body))
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return ArtifactRef(str(relative_path), expected_hash, len(body))

    def _resolve_relative(self, relative_path: str) -> Path:
        """把数据库相对路径限制到当前 ``data_root/discussions``。

        Args:
            relative_path: 不能是绝对路径或包含越界片段的引用。

        Returns:
            已解析且位于 discussion 根下的绝对路径。

        Raises:
            ValueError: 路径不属于当前 discussion 根。
        """

        raw = Path(relative_path)
        if raw.is_absolute():
            raise ValueError("discussion artifact path must be relative")
        resolved = (self.data_root / raw).resolve()
        try:
            resolved.relative_to(self._discussion_root.resolve())
        except ValueError as exc:
            raise ValueError(
                "discussion artifact path escapes discussion root"
            ) from exc
        return resolved

    @staticmethod
    def _round_dir(discussion_id: str, round_number: int) -> Path:
        """返回一轮 artifact 的相对目录。

        Args:
            discussion_id: 所属研讨 ID。
            round_number: 从 1 开始的轮号。

        Returns:
            ``discussions/<id>/rounds/<六位轮号>``。
        """

        return Path("discussions") / discussion_id / "rounds" / f"{round_number:06d}"

    @classmethod
    def _attempt_dir(
        cls,
        discussion_id: str,
        round_number: int,
        participant_id: str,
        ordinal: int,
        attempt_id: str,
    ) -> Path:
        """返回一个 participant attempt 的相对目录。

        Args:
            discussion_id: 所属研讨 ID。
            round_number: 所属轮号。
            participant_id: 所属 participant ID。
            ordinal: 尝试序号。
            attempt_id: 尝试稳定 ID。

        Returns:
            attempt 私有目录。
        """

        return (
            cls._round_dir(discussion_id, round_number)
            / "participants"
            / participant_id
            / "attempts"
            / f"{ordinal:03d}-{attempt_id}"
        )

    @staticmethod
    def _json_bytes(payload: dict[str, Any]) -> bytes:
        """把 JSON object 编码成稳定、可读的 UTF-8 字节。

        Args:
            payload: 要编码的对象。

        Returns:
            末尾带换行的规范化 JSON。
        """

        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
