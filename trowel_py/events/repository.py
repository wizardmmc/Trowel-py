"""读写事件日志和各类事件最近一次触发时间。"""

import sqlite3
import uuid
from datetime import datetime

from trowel_py.events.cooldown import Cooldowns
from trowel_py.events.types import EventType
from trowel_py.events.models import EventLog


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
    """用事件执行结果构建默认玩家的日志模型。"""

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
    """为给定数据库连接创建事件数据读写对象。"""

    return EventRepository(conn)


class EventRepository:
    """通过 SQLite 保存和查询事件日志及冷却时间。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """绑定由调用方管理事务和生命周期的数据库连接。"""

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
        """在当前事务中写入事件日志，并返回对应模型。"""

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
        """按触发时间从新到旧读取指定数量的事件日志。"""

        rows = self.conn.execute(
            "select * from event_log order by triggered_at desc limit ?",
            (limit,),
        ).fetchall()
        return [self._row_to_event_log(row) for row in rows]

    def get_recent_card_ids(self, event_type: EventType, limit: int) -> list[str]:
        """读取某类事件最近关联的非空卡片 ID。"""

        rows = self.conn.execute(
            "select card_id from event_log where event_type = ? and card_id is not null "
            "order by triggered_at desc limit ?",
            (event_type, limit),
        ).fetchall()
        return [row["card_id"] for row in rows]

    def get_last_triggered_map(self) -> Cooldowns:
        """读取每类事件最近一次触发时间。"""

        rows = self.conn.execute(
            "select event_type, last_triggered from event_cooldowns"
        ).fetchall()
        return {
            row["event_type"]: datetime.fromisoformat(row["last_triggered"])
            for row in rows
        }

    def upsert_cooldown(self, event_type: EventType, now: datetime) -> None:
        """新增或更新一种事件的最近触发时间。"""

        self.conn.execute(
            "insert or replace into event_cooldowns (event_type, last_triggered) values (?, ? )",
            (event_type, now.isoformat()),
        )

    def _row_to_event_log(self, row: sqlite3.Row) -> EventLog:
        """把 SQLite 行转换为事件日志模型。"""

        data = dict(row)
        data["triggered_at"] = datetime.fromisoformat(data["triggered_at"])
        return EventLog(**data)
