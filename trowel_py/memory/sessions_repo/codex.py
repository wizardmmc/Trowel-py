"""Codex turns、fragment 领取与提炼水位的仓储。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from hashlib import sha256

from .database import row_to_codex_turn
from .models import CodexPendingFragment, CodexTurnRecord


def _fragment_id(turns: list[CodexTurnRecord]) -> str:
    """由确定的 thread 和有序 turn 集合生成稳定 fragment ID。"""
    first = turns[0]
    if len(turns) == 1:
        return f"codex:{first.thread_id}:{first.turn_id}"
    digest = sha256(
        "\0".join(turn.turn_id for turn in turns).encode("utf-8")
    ).hexdigest()[:16]
    return f"codex:{first.thread_id}:fragment:{digest}"


class CodexTurnsRepository:
    """管理 ``codex_turns`` 表及 fragment 的原子领取和推进。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """复用 composition root 已初始化的 SQLite 连接。"""
        self._conn = conn

    def register_turn(
        self,
        *,
        thread_id: str,
        turn_id: str,
        trowel_session_id: str,
        workdir: str,
        journal_path: str,
        registered_at: str,
        model: str,
        effort: str,
        provider: str,
        memory_enabled: bool,
        profile_enabled: bool,
        session_kind: str = "user",
    ) -> None:
        """首次事件登记 turn；重放同一原生 turn 时不覆盖原始身份。"""
        self._conn.execute(
            "INSERT OR IGNORE INTO codex_turns"
            " (thread_id, turn_id, trowel_session_id, workdir, journal_path,"
            " registered_at, model, effort, provider, memory_enabled,"
            " profile_enabled, session_kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                turn_id,
                trowel_session_id,
                workdir,
                journal_path,
                registered_at,
                model,
                effort,
                provider,
                int(memory_enabled),
                int(profile_enabled),
                session_kind,
            ),
        )
        self._conn.commit()

    def complete_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        status: str,
        completed_at: str,
    ) -> None:
        """写入 Codex 原生 turn 的终态和完成时间。"""
        if status not in {"completed", "interrupted", "failed"}:
            raise ValueError(f"unknown Codex terminal status: {status}")
        cursor = self._conn.execute(
            "UPDATE codex_turns SET status = ?, completed_at = ?"
            " WHERE thread_id = ? AND turn_id = ?",
            (status, completed_at, thread_id, turn_id),
        )
        if cursor.rowcount != 1:
            raise KeyError((thread_id, turn_id))
        self._conn.commit()

    def list_completed_user_turns(self) -> list[CodexTurnRecord]:
        """返回全部已封口用户 turns，供独立下游按自身水位筛选。

        查询不读取 ``extracted_at``、``review_fragment_id``、
        ``profile_enabled`` 或 ``memory_enabled``，因此 Memory 处理状态和会话
        启动时的注入开关不会改变其他流水线的候选范围。
        """
        rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " WHERE completed_at IS NOT NULL AND session_kind = 'user'"
            " ORDER BY completed_at, registered_at, thread_id, turn_id"
        ).fetchall()
        return [row_to_codex_turn(row) for row in rows]

    def list_attribution_turns(self) -> list[CodexTurnRecord]:
        """返回归因索引需要的全部 Codex turn 身份。

        归因必须同时覆盖用户会话与内部会话，因此不能复用只读取已封口用户
        turn 的查询。调用方只使用 thread、Trowel session 和 session kind，不把
        其他字段公开到统计结果。

        Returns:
            按登记时间稳定排列的全部 Codex turn 记录。
        """

        rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " ORDER BY registered_at, thread_id, turn_id"
        ).fetchall()
        return [row_to_codex_turn(row) for row in rows]

    def list_completed_for_trowel_session(
        self,
        trowel_session_id: str,
    ) -> list[CodexTurnRecord]:
        """返回一个 Trowel 用户会话中全部已封口 Codex turns。

        Args:
            trowel_session_id: Trowel 分配且在首次 turn 事件登记的会话 ID。

        Returns:
            按完成、登记时间和原生 turn ID 稳定排列的用户 turns。
        """

        rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " WHERE trowel_session_id = ? AND completed_at IS NOT NULL"
            " AND session_kind = 'user'"
            " ORDER BY completed_at, registered_at, thread_id, turn_id",
            (trowel_session_id,),
        ).fetchall()
        return [row_to_codex_turn(row) for row in rows]

    def list_replayable_thread_turns(self, thread_id: str) -> list[CodexTurnRecord]:
        """返回指定 thread 中已有完整终态日志的 turns。

        本查询只供会话回放读取 normalized journal，不受 Memory/Profile 开关、
        提炼水位或会话类别影响。尚未封口的 turn 可能仍停留在进程缓冲区，因此不
        作为可靠的历史源返回。

        Args:
            thread_id: Codex 原生 thread ID。
        """
        rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " WHERE thread_id = ? AND completed_at IS NOT NULL"
            " ORDER BY completed_at, registered_at, turn_id",
            (thread_id,),
        ).fetchall()
        return [row_to_codex_turn(row) for row in rows]

    def claim_pending_fragments(
        self,
        *,
        completed_before: str | None = None,
        trowel_session_id: str | None = None,
    ) -> list[CodexPendingFragment]:
        """领取并返回尚未提炼的用户 Codex fragments。

        与普通 ``find`` 不同，本方法会把首次领取的成员关系写入数据库。失败
        重试继续返回原 fragment，不会吸收后来完成的新 turn。
        """
        target_sql = ""
        params: tuple[str, ...] = ()
        if trowel_session_id is not None:
            target_sql = (
                " AND ((trowel_session_id = ? AND review_fragment_id = '')"
                " OR (trowel_session_id = ? AND review_fragment_id != ''"
                " AND NOT EXISTS ("
                " SELECT 1 FROM codex_turns AS fragment_member"
                " WHERE fragment_member.review_fragment_id"
                " = codex_turns.review_fragment_id"
                " AND fragment_member.completed_at IS NOT NULL"
                " AND fragment_member.extracted_at IS NULL"
                " AND fragment_member.trowel_session_id != ?"
                " )))"
            )
            params = (
                trowel_session_id,
                trowel_session_id,
                trowel_session_id,
            )
        pending_rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " WHERE completed_at IS NOT NULL AND extracted_at IS NULL"
            " AND session_kind = 'user'"
            + target_sql
            + " ORDER BY completed_at, registered_at, thread_id, turn_id",
            params,
        ).fetchall()
        assigned: dict[str, list[CodexTurnRecord]] = defaultdict(list)
        unassigned: dict[str, list[CodexTurnRecord]] = defaultdict(list)
        for row in pending_rows:
            turn = row_to_codex_turn(row)
            if turn.review_fragment_id:
                assigned[turn.review_fragment_id].append(turn)
            elif completed_before is None or (
                turn.completed_at is not None and turn.completed_at < completed_before
            ):
                unassigned[turn.thread_id].append(turn)

        claimed: list[CodexPendingFragment] = []
        for turns in unassigned.values():
            fragment_id = _fragment_id(turns)
            turn_ids = tuple(turn.turn_id for turn in turns)
            placeholders = ",".join("?" for _ in turn_ids)
            self._conn.execute("SAVEPOINT claim_codex_fragment")
            cursor = self._conn.execute(
                "UPDATE codex_turns SET review_fragment_id = ?"
                " WHERE thread_id = ?"
                f" AND turn_id IN ({placeholders})"
                " AND review_fragment_id = ''"
                " AND completed_at IS NOT NULL AND extracted_at IS NULL",
                (fragment_id, turns[0].thread_id, *turn_ids),
            )
            if cursor.rowcount != len(turns):
                self._conn.execute("ROLLBACK TO claim_codex_fragment")
                self._conn.execute("RELEASE claim_codex_fragment")
                raise RuntimeError("Codex fragment claim changed concurrently")
            self._conn.execute("RELEASE claim_codex_fragment")
            assigned_turns = tuple(
                replace(turn, review_fragment_id=fragment_id) for turn in turns
            )
            claimed.append(CodexPendingFragment(fragment_id, assigned_turns))
        if claimed:
            self._conn.commit()

        existing = [
            CodexPendingFragment(fragment_id, tuple(turns))
            for fragment_id, turns in assigned.items()
            if completed_before is None
            or all(
                turn.completed_at is not None and turn.completed_at < completed_before
                for turn in turns
            )
        ]
        fragments = existing + claimed
        fragments.sort(
            key=lambda fragment: (
                fragment.turns[0].completed_at or "",
                fragment.turns[0].registered_at,
                fragment.thread_id,
                fragment.turns[0].turn_id,
            )
        )
        return fragments

    def list_extracted_before(
        self,
        fragment: CodexPendingFragment,
    ) -> tuple[CodexTurnRecord, ...]:
        """返回同一 thread 中排在目标 fragment 前面的已提炼用户 turns。"""
        rows = self._conn.execute(
            "SELECT * FROM codex_turns"
            " WHERE thread_id = ? AND completed_at IS NOT NULL"
            " AND session_kind = 'user'"
            " ORDER BY completed_at, registered_at, turn_id",
            (fragment.thread_id,),
        ).fetchall()
        turns = tuple(row_to_codex_turn(row) for row in rows)
        positions = {turn.turn_id: index for index, turn in enumerate(turns)}
        try:
            first_target = min(positions[turn_id] for turn_id in fragment.turn_ids)
        except KeyError as exc:
            raise ValueError("Codex fragment turn is missing from repository") from exc
        return tuple(
            turn for turn in turns[:first_target] if turn.extracted_at is not None
        )

    def list_unsealed_turns(self) -> list[CodexTurnRecord]:
        """返回尚未记录完成时间的全部 Codex turns，供日志修复使用。"""
        rows = self._conn.execute(
            "SELECT * FROM codex_turns WHERE completed_at IS NULL"
            " ORDER BY registered_at, thread_id, turn_id"
        ).fetchall()
        return [row_to_codex_turn(row) for row in rows]

    def advance_turn(
        self,
        thread_id: str,
        turn_id: str,
        *,
        when: str | None = None,
    ) -> None:
        """推进指定 turn；已领取时原子推进其整个 fragment。"""
        row = self._conn.execute(
            "SELECT review_fragment_id FROM codex_turns"
            " WHERE thread_id = ? AND turn_id = ?"
            " AND completed_at IS NOT NULL AND extracted_at IS NULL",
            (thread_id, turn_id),
        ).fetchone()
        if row is None:
            self._conn.commit()
            return
        fragment_id = row["review_fragment_id"] or ""
        if fragment_id:
            rows = self._conn.execute(
                "SELECT turn_id FROM codex_turns"
                " WHERE thread_id = ? AND review_fragment_id = ?"
                " AND completed_at IS NOT NULL AND extracted_at IS NULL"
                " ORDER BY completed_at, registered_at, turn_id",
                (thread_id, fragment_id),
            ).fetchall()
            turn_ids = tuple(str(item["turn_id"]) for item in rows)
        else:
            turn_ids = (turn_id,)
        self.advance_fragment(thread_id, turn_ids, when=when)

    def advance_fragment(
        self,
        thread_id: str,
        turn_ids: tuple[str, ...],
        *,
        when: str | None = None,
    ) -> None:
        """在一个 SQLite 事务中推进 fragment 的全部 turn 水位。"""
        if not turn_ids or len(turn_ids) != len(set(turn_ids)):
            raise ValueError("Codex atomic advance requires unique turn ids")
        stamp = when or datetime.now().isoformat()
        placeholders = ",".join("?" for _ in turn_ids)
        self._conn.execute("SAVEPOINT advance_codex_fragment")
        rows = self._conn.execute(
            "SELECT review_fragment_id FROM codex_turns"
            " WHERE thread_id = ?"
            f" AND turn_id IN ({placeholders})"
            " AND completed_at IS NOT NULL AND extracted_at IS NULL",
            (thread_id, *turn_ids),
        ).fetchall()
        fragment_ids = {str(row["review_fragment_id"] or "") for row in rows}
        if len(rows) != len(turn_ids) or len(fragment_ids) != 1:
            self._conn.execute("ROLLBACK TO advance_codex_fragment")
            self._conn.execute("RELEASE advance_codex_fragment")
            raise ValueError("Codex fragment could not advance atomically")
        cursor = self._conn.execute(
            "UPDATE codex_turns SET extracted_at = ?"
            " WHERE thread_id = ?"
            f" AND turn_id IN ({placeholders})"
            " AND completed_at IS NOT NULL AND extracted_at IS NULL",
            (stamp, thread_id, *turn_ids),
        )
        if cursor.rowcount != len(turn_ids):
            self._conn.execute("ROLLBACK TO advance_codex_fragment")
            self._conn.execute("RELEASE advance_codex_fragment")
            raise ValueError("Codex fragment could not advance atomically")
        self._conn.execute("RELEASE advance_codex_fragment")
        self._conn.commit()
