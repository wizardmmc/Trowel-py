import sqlite3
import json
from trowel_py.schemas.card import Card 

def create_card_repository(conn: sqlite3.Connection):
    return CardRepository(conn)


class CardRepository:
    """
    intermediate layer that hides SQL details behind CRUD methods
    """
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(self, card: Card) -> Card:
        """
        insert a new card record
        """
        data = card.model_dump()    # convert pydantic model to dict
        data["tags"] = json.dumps(data["tags"]) # convert tags list to JSON string
        data["created_at"] = data["created_at"].isoformat() # convert datetime to ISO string for SQLite
        data["updated_at"] = data["updated_at"].isoformat()
        self.conn.execute(
            "insert into cards (id, title, category, explanation, example, difficulty, source, tags, status, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (data["id"], data["title"], data["category"], data["explanation"], data["example"], data["difficulty"], data["source"], data["tags"], data["status"], data["created_at"], data["updated_at"])
        )
        return card # why need to return pydantic data?

    def find_by_id(self, card_id: str) -> Card | None:
        """
        find a card by its ID
        """
        # res: sqlite3.Row | None
        res = self.conn.execute(
            "select * from cards where id = (?)",
            (card_id, )).fetchone()
        if res is None:
            return None
        row_dict = dict(res)
        row_dict["tags"] = json.loads(row_dict["tags"])
        return Card(**row_dict)    # equals Card(id=row_dict["id"], ......)
    
    def find_all(self) -> list[Card]:
        """
        retrieve all card records
        """
        rows = self.conn.execute(
            "select * from cards"
        ).fetchall()
        res = []
        for row in rows:
            row_dict = dict(row)
            row_dict["tags"] = json.loads(row_dict["tags"])
            res.append(Card(**row_dict))
        return res

    def update(self, card_id: str, new_card: Card) -> Card:
        """
        replace the whole card data
        """
        data = new_card.model_dump()
        data["tags"] = json.dumps(data["tags"])
        data["created_at"] = data["created_at"].isoformat()
        data["updated_at"] = data["updated_at"].isoformat()
        self.conn.execute(
            "update cards set title=?, category=?, explanation=?, example=?, difficulty=?, source=?, tags=?, status=?, created_at=?, updated_at=? where id=?", (data["title"], data["category"], data["explanation"], data["example"], data["difficulty"], data["source"], data["tags"], data["status"], data["created_at"], data["updated_at"], card_id, ))
        return new_card

    def search_by_fts5(self, query: str) -> list[Card]:
        """
        search cards using full-text search
        """
        rows = self.conn.execute(
            "select * from cards where rowid in (select rowid from cards_fts where cards_fts match ?)", (query, )
        ).fetchall()
        res = []
        for row in rows:
            row_dict = dict(row)
            row_dict["tags"] = json.loads(row_dict["tags"])
            res.append(Card(**row_dict))
        return res

