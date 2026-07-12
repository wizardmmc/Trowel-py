"""UUIDv7 generation for stable, time-ordered memory ids (slice-041 D1).

UUIDv7 (RFC 9562): 48-bit Unix-ms timestamp + version 7 + random tail. The
timestamp occupies the high bits, so lexicographic string sort == creation-time
sort — "便于按创建时间排查" (D1). ``content_hash`` (040-a) stays the idempotence
key; ``memory_id`` is the stable identity that survives title/slug edits and
threads the supersedes chain.

Python stdlib has no ``uuid7`` until 3.14; this is a minimal hand-rolled impl.
``now_ms`` is injectable so tests can pin the timestamp and assert ordering.
"""
from __future__ import annotations

import os
import time
import uuid


def uuid7(*, now_ms: int | None = None) -> uuid.UUID:
    """Generate a UUIDv7 (RFC 9562).

    Args:
        now_ms: injectable Unix-ms timestamp (for tests); None uses the wall
            clock. Injecting makes the timestamp deterministic without freezing
            the random tail, so two calls at the same ms still differ.

    Returns:
        A UUIDv7 whose first 48 bits encode ``now_ms``.
    """
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    ts = ms.to_bytes(6, "big")  # 48-bit timestamp
    rand = os.urandom(10)
    b = bytearray(ts) + bytearray(rand)
    b[6] = (b[6] & 0x0F) | 0x70  # version 7
    b[8] = (b[8] & 0x3F) | 0x80  # variant 10
    return uuid.UUID(bytes=bytes(b))
