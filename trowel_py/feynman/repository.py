"""费曼学习会话的持久化边界。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FeynmanSession:
    id: str
    card_id: str
    question: str
    user_answer: str | None = None
    accuracy: int | None = None
    completeness: int | None = None
    feedback: str | None = None
    missed_points: list[str] | None = None
    created_at: str | None = None


def create_feynman_repository(conn: sqlite3.Connection) -> FeynmanRepository:
    return FeynmanRepository(conn)


class FeynmanRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_by_id(self, session_id: str) -> FeynmanSession | None:
        row = self.conn.execute(
            "select id, card_id, question, user_answer, accuracy, completeness, feedback, missed_points, created_at from feynman_sessions where id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def find_by_card_id(self, card_id: str) -> list[FeynmanSession]:
        """按数据库时间降序返回卡片的学习会话。"""
        rows = self.conn.execute(
            "select id, card_id, question, user_answer, accuracy, completeness, feedback, missed_points, created_at from feynman_sessions where card_id = ? order by created_at desc",
            (card_id,),
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def create(self, session: FeynmanSession) -> FeynmanSession:
        """只写入提问阶段字段；外键错误由 SQLite 原样抛出。"""
        cursor = self.conn.execute(
            "insert into feynman_sessions (id, card_id, question) values (?, ?, ?)",
            (session.id, session.card_id, session.question),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"feynman session insert affected {cursor.rowcount} rows, expected 1"
            )
        return session

    def update_with_evaluation(
        self,
        session_id: str,
        user_answer: str,
        accuracy: int,
        completeness: int,
        feedback: str,
        missed_points: list[str],
    ) -> None:
        """缺失会话必须失败，避免把评估写入静默降为无操作。"""
        cursor = self.conn.execute(
            "update feynman_sessions set user_answer = ?, accuracy = ?, "
            "completeness = ?, feedback = ?, missed_points = ? where id = ?",
            (
                user_answer,
                accuracy,
                completeness,
                feedback,
                json.dumps(missed_points),
                session_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"feynman session update affected {cursor.rowcount} rows, expected 1"
            )

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> FeynmanSession:
        d = dict(row)
        if d["missed_points"] is not None:
            d["missed_points"] = json.loads(d["missed_points"])
        return FeynmanSession(**d)
