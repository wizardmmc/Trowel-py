"""fcntl lock coordinating dictionary readers vs the rebuild publisher.

slice-064 C-2: the rebuild publish path takes an exclusive lock; the MCP search
path (and the read-only check) take a shared lock. This prevents a concurrent
rebuild from swapping/deleting an L1 file while a reader is mid-retrieval, and
prevents two rebuilds from racing on the same ``dictionary-L1`` dir.

Lives in its own module so both ``dictionary`` (which imports the check) and
``dictionary_check`` (which the check's own SH lock needs) can import it without
a cycle. Off-Unix (``fcntl`` is None) the lock is a no-op — mutual exclusion
then relies on the caller (review_lock / tidy_lock) being single-instance in
practice, and on the next check rebuilding after any torn write.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

try:  # Unix-only; Windows has no flock → the lock becomes a no-op there.
    import fcntl
except ImportError:  # pragma: no cover — non-Unix
    fcntl = None  # type: ignore[assignment]

_DICT_LOCK_REL = "meta/.dictionary.lock"


@contextlib.contextmanager
def dictionary_lock(root: Path | str, *, exclusive: bool):
    """``exclusive=True`` → LOCK_EX (rebuild publish); ``False`` → LOCK_SH (read)."""
    if fcntl is None:
        yield
        return
    lock_path = Path(root) / _DICT_LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
