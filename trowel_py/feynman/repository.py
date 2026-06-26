"""
repo for the feynman_sessions table
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FeynmanSession:
    """
    one row of feynman_sessions
    """

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
    """
    CRUD over feynman_sessions
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def find_by_id(self, session_id: str) -> FeynmanSession | None:
        """
        return one session by id, or None if it doesn't exist

        Args:
            session_id: the session to load.

        Returns:
            the session, with missed_points parsed back from JSON text to a
            list; or None when the id is unknown.
        """
        row = self.conn.execute(
            "select id, card_id, question, user_answer, accuracy, completeness, feedback, missed_points, created_at from feynman_sessions where id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def find_by_card_id(self, card_id: str) -> list[FeynmanSession]:
        """
        return all sessions for a card, newest first

        Args:
            card_id: the card whose sessions to load.

        Returns:
            sessions ordered by created_at descending (newest first) — the
            history view reads top-down. empty list if the card has none.
        """
        rows = self.conn.execute(
            "select id, card_id, question, user_answer, accuracy, completeness, feedback, missed_points, created_at from feynman_sessions where card_id = ? order by created_at desc",
            (card_id,),
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def create(self, session: FeynmanSession) -> FeynmanSession:
        """
        insert a new session at the question-generated stage

        Args:
            session: the session to insert. card_id must reference an existing
                card, else sqlite raises IntegrityError (FK violation) — let
                it propagate so the caller knows the insert failed.

        Returns:
            the inserted session (echoed back for call-site convenience).

        Raises:
            sqlite3.IntegrityError: card_id does not exist (FK violation).
        """
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
        """
        fill in the evaluation fields of an existing session.

        Args:
            session_id: the session to update.
            user_answer: the user's explanation.
            accuracy: factual correctness 0-100.
            completeness: key-point coverage 0-100.
            feedback: the judge's pointed feedback.
            missed_points: key points the user left out; stored as JSON text.

        Raises:
            RuntimeError: no row matched session_id (the session never existed
                or was deleted). fail-fast — a silent no-op update would hide
                a missing session from the caller.
        """
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
        """
        turn a DB row into a FeynmanSession, parsing missed_points JSON

        Args:
            row: a feynman_sessions row.

        Returns:
            the session; missed_points is None when the column is null
            (unevaluated session) or a list[str] after evaluation.
        """
        d = dict(row)
        if d["missed_points"] is not None:
            d["missed_points"] = json.loads(d["missed_points"])
        return FeynmanSession(**d)
