"""生成带毫秒时间前缀的 Memory UUIDv7。

高 48 位保存 Unix 毫秒时间戳，因此不同毫秒的 UUID 文本顺序反映时间戳顺序。
同一毫秒内的剩余 74 位来自系统随机源，因此不保证单调，仍有极低的碰撞概率；
系统时钟回退时，本模块也不会修正顺序。
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7(*, now_ms: int | None = None) -> uuid.UUID:
    """按 RFC 9562 生成一个 UUIDv7。

    Args:
        now_ms: 写入高 48 位的 Unix 毫秒时间戳；为 ``None`` 时读取当前墙上
            时钟。该参数不固定其余随机位。

    Returns:
        版本为 7、variant 位模式为 ``0b10``（Python 中对应
        ``uuid.RFC_4122``）的 UUID。

    Raises:
        OverflowError: 时间戳超出无符号 48 位范围。
    """
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    ts = ms.to_bytes(6, "big")
    rand = os.urandom(10)
    b = bytearray(ts) + bytearray(rand)
    b[6] = (b[6] & 0x0F) | 0x70  # 高 4 位写入版本 7，保留低 4 个随机位。
    b[8] = (b[8] & 0x3F) | 0x80  # 高 2 位设为 RFC 9562 variant 位模式 0b10，保留低 6 个随机位。
    return uuid.UUID(bytes=bytes(b))
