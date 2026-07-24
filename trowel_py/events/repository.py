import sqlite3
import uuid
from datetime import datetime

from trowel_py.events.cooldown import Cooldowns
from trowel_py.events.types import EventType
from trowel_py.schemas.event import EventLog


def _event_log(
    event_id: str,
    event_type: EventType,
    description: str | None,
    xp: int,
    coins: int,
    item_id: str | None,
    card_id: str | None,
    now: datetime,
) -> EventLog:
    return EventLog(
        id=event_id,
        player_id="default",
        event_type=event_type,
        description=description,
        reward_xp=xp,
        reward_coin=coins,
        reward_item_id=item_id,
        card_id=card_id,
        triggered_at=now,
    )


def create_event_repository(conn: sqlite3.Connection):
    return EventRepository(conn)


class EventRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def record_event(
        self,
        event_type: EventType,
        description: str | None,
        xp: int,
        coins: int,
        item_id: str | None,
        card_id: str | None,
        now: datetime,
    ) -> EventLog:
        event_id = uuid.uuid4().hex[:12]
        self.conn.execute(
            "insert into event_log (id, player_id, event_type, description, "
            "reward_xp, reward_coin, reward_item_id, card_id, triggered_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                "default",
                event_type,
                description,
                xp,
                coins,
                item_id,
                card_id,
                now.isoformat(),
            ),
        )
        return _event_log(
            event_id,
            event_type,
            description,
            xp,
            coins,
            item_id,
            card_id,
            now,
        )

    def get_recent(self, limit: int) -> list[EventLog]:
        rows = self.conn.execute(
            "select * from event_log order by triggered_at desc limit ?",
            (limit,),
        ).fetchall()
        return [self._row_to_event_log(row) for row in rows]

    def get_recent_card_ids(self, event_type: EventType, limit: int) -> list[str]:
        rows = self.conn.execute(
            "select card_id from event_log where event_type = ? and card_id is not null "
            "order by triggered_at desc limit ?",
            (event_type, limit),
        ).fetchall()
        return [row["card_id"] for row in rows]

    def get_last_triggered_map(self) -> Cooldowns:
        rows = self.conn.execute(
            "select event_type, last_triggered from event_cooldowns"
        ).fetchall()
        return {
            row["event_type"]: datetime.fromisoformat(row["last_triggered"])
            for row in rows
        }

    def upsert_cooldown(self, event_type: EventType, now: datetime) -> None:
        self.conn.execute(
            "insert or replace into event_cooldowns (event_type, last_triggered) values (?, ? )",
            (event_type, now.isoformat()),
        )

    def _row_to_event_log(self, row: sqlite3.Row) -> EventLog:
        data = dict(row)
        data["triggered_at"] = datetime.fromisoformat(data["triggered_at"])
        return EventLog(**data)
