"""生成按时间有序的稳定 memory UUIDv7。

高 48 位保存 Unix 毫秒时间戳，因此字符串排序与创建时间一致；随机尾部保证同一
毫秒内仍能生成不同身份。
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7(*, now_ms: int | None = None) -> uuid.UUID:
    """按 RFC 9562 生成 UUIDv7；``now_ms`` 仅固定时间部分。"""
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    ts = ms.to_bytes(6, "big")
    rand = os.urandom(10)
    b = bytearray(ts) + bytearray(rand)
    b[6] = (b[6] & 0x0F) | 0x70  # 版本位设为 7。
    b[8] = (b[8] & 0x3F) | 0x80  # variant 位模式设为 10。
    return uuid.UUID(bytes=bytes(b))
