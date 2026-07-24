import json
import sqlite3

from trowel_py.cards.models import Card


def _card_record(card: Card) -> dict[str, object]:
    data = card.model_dump()
    data["tags"] = json.dumps(data["tags"])
    data["created_at"] = data["created_at"].isoformat()
    data["updated_at"] = data["updated_at"].isoformat()
    return data


def _card_from_row(row: sqlite3.Row) -> Card:
    data = dict(row)
    data["tags"] = json.loads(data["tags"])
    return Card(**data)


def create_card_repository(conn: sqlite3.Connection):
    return CardRepository(conn)


class CardRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, card: Card) -> Card:
        data = _card_record(card)
        self.conn.execute(
            "insert into cards "
            "(id, title, category, explanation, example, difficulty, source, "
            "tags, status, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data["id"],
                data["title"],
                data["category"],
                data["explanation"],
                data["example"],
                data["difficulty"],
                data["source"],
                data["tags"],
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )
        return card

    def find_by_id(self, card_id: str) -> Card | None:
        res = self.conn.execute(
            "select * from cards where id = (?)", (card_id,)
        ).fetchone()
        if res is None:
            return None
        return _card_from_row(res)

    def find_all(self) -> list[Card]:
        rows = self.conn.execute("select * from cards").fetchall()
        return [_card_from_row(row) for row in rows]

    def update(self, card_id: str, new_card: Card) -> Card:
        data = _card_record(new_card)
        self.conn.execute(
            "update cards set title=?, category=?, explanation=?, example=?, "
            "difficulty=?, source=?, tags=?, status=?, created_at=?, "
            "updated_at=? where id=?",
            (
                data["title"],
                data["category"],
                data["explanation"],
                data["example"],
                data["difficulty"],
                data["source"],
                data["tags"],
                data["status"],
                data["created_at"],
                data["updated_at"],
                card_id,
            ),
        )
        return new_card

    def search_by_fts5(self, query: str) -> list[Card]:
        rows = self.conn.execute(
            "select * from cards where rowid in "
            "(select rowid from cards_fts where cards_fts match ?)",
            (query,),
        ).fetchall()
        return [_card_from_row(row) for row in rows]
