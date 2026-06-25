"""
repo for the card_explanation_history table
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ExplanationHistoryEntry:
    """
    one row of card_explanation_history

    Attributes:
        id: row id.
        card_id: the card this explanation belongs to.
        explanation: the explanation text at this point in time.
        source: where it came from. mirrors the DB CHECK constraint
            ('original' | 'llm' | 'user'); kept as a plain str here because
            the DB already enforces it.
        created_at: ISO timestamp string (sqlite stores text).
    """
    id: str
    card_id: str
    explanation: str
    source: str # i saw migration set default ,why you write so easy? because it's not pydantic model?
    created_at: str


def create_explanation_history_repository(conn: sqlite3.Connection) -> ExplanationHistoryRepository:
    return ExplanationHistoryRepository(conn)


class ExplanationHistoryRepository:
    """
    CRUD over card_explanation_history
    """
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn


    def find_by_card_id(self, card_id: str) -> list[ExplanationHistoryEntry]:
        """
        return all history rows for a card, oldest first

        Args:
            card_id: the card whose explanation history to load.

        Returns:
            history entries ordered by created_at ascending (oldest first),
            so the caller can show a timeline. empty list if the card has none.
        """
        rows = self.conn.execute(
            "select id, card_id, explanation, source, created_at from card_explanation_history where card_id = ? order by created_at asc",
            (card_id, ), 
        ).fetchall()
        return [ExplanationHistoryEntry(**dict(row)) for row in rows]
    

    def find_latest(self, card_id: str) -> ExplanationHistoryEntry | None:
        """
        return the most recent explantion for a card, or None if it has none

        Args:
            card_id: the card whose latest explanation to fetch.

        Returns:
            the newest entry, or None when the card has no history yet.
        """
        row = self.conn.execute(
            "select id, card_id, explanation, source, created_at from card_explanation_history where card_id = ? order by created_at desc limit 1", 
            (card_id, ), 
        ).fetchone()
        if row is None:
            return None
        return ExplanationHistoryEntry(**dict(row))
    

    def create(self, entry: ExplanationHistoryEntry) -> ExplanationHistoryEntry:
        """
        append a new explanation version. history is append-only.

        Args:
            entry: the history row to insert. card_id must reference an
                existing card, else sqlite raises IntegrityError (FK violation)
                — let it propagate so the caller knows the insert failed.

        Returns:
            the inserted entry (echoed back for call-site convenience).

        Raises:
            sqlite3.IntegrityError: card_id does not exist (FK violation).
        """
        cursor = self.conn.execute(
            "insert into card_explanation_history (id, card_id, explanation, source) "
            "values (?, ?, ?, ?)", 
            (entry.id, entry.card_id, entry.explanation, entry.source), 
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"explanation history insert affected {cursor.rowcount} rows, expected 1"
            )
        return entry

    