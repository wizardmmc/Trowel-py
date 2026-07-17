"""extract the real activity dates of one jsonl byte segment (slice-061 block-3).

Scans the ``[start, end)`` byte range of a cc session jsonl and collects the
calendar dates of every timestamped user/assistant event, converted to trowel's
local timezone. This is the ground truth for where a segment's diary entries
belong (C-1: activity dates come from raw jsonl timestamps, NEVER from the
review run day — that was the root cause of补跑旧会话内容落进今天).

When the segment has no usable timestamp it falls back to
``last_completed_at`` → ``registered_at`` (C-1), and stamps ``date_basis`` so the
fallback is auditable. A segment with neither timestamps nor fallbacks yields an
empty ``dates`` tuple (the caller treats that as "no attributable day" rather
than guessing the run day).

Byte-boundary handling: ``start``/``end`` are jsonl byte watermarks and need
not fall on a line boundary. A partial line at ``start`` is dropped (its
timestamp may be severed); a line that crosses ``end`` is also dropped (it is
the next segment's responsibility). Each kept line is parsed leniently — a
corrupt/non-JSON line is skipped, never fatal.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import IO, Literal

DateBasis = Literal["jsonl_timestamp", "completed_at", "registered_at"]

#: cc jsonl row types that carry a real interaction timestamp. ``tool`` results
#: are nested inside ``user``/``assistant`` messages, so these two cover the
#: ground truth of "something happened on this day".
_TIMESTAMPED_TYPES = frozenset({"user", "assistant"})


@dataclass(frozen=True)
class ActivityDates:
    """The calendar dates one jsonl segment actually touches, + how we know.

    Attributes:
        dates: sorted unique ``YYYY-MM-DD`` strings observed in the segment.
        basis: ``jsonl_timestamp`` when dates came from real events;
            ``completed_at`` / ``registered_at`` when the segment had no usable
            timestamp and the date came from a recorded fallback (C-1).
    """

    dates: tuple[str, ...]
    basis: DateBasis


def _system_local_tz() -> tzinfo | None:
    """trowel's local timezone (the wall clock's tz). Inject ``local_tz`` in tests."""
    return datetime.now().astimezone().tzinfo


def _parse_iso_to_date(raw: str, tz: tzinfo | None) -> str | None:
    """Parse an ISO-8601 timestamp (with ``Z`` or offset) to a local YYYY-MM-DD.

    Returns None when ``raw`` is empty or not parseable — the caller treats that
    as "no date here" and moves down the fallback chain.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    # Python <3.11 ``fromisoformat`` rejects a trailing ``Z``; normalize it.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # a naive timestamp is treated as LOCAL time: the only naive inputs are
        # the fallback stamps (registered_at / last_completed_at, from
        # datetime.now().isoformat()), which are wall-clock local. cc jsonl
        # timestamps always carry ``Z`` (UTC) and never reach this branch.
        dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return dt.astimezone(tz).date().isoformat()


def _date_of_line(line: bytes, tz: tzinfo | None) -> str | None:
    """Extract the local date of one jsonl line, or None if not applicable.

    Only ``user``/``assistant`` rows count (attachment/queue-operation/system
    rows carry timestamps but mirror a sibling interaction and would double it).
    A corrupt or non-JSON line yields None (skipped, never fatal).
    """
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") not in _TIMESTAMPED_TYPES:
        return None
    return _parse_iso_to_date(str(obj.get("timestamp", "")), tz)


def extract_activity_dates(
    jsonl_path: Path | str,
    start: int,
    end: int,
    *,
    last_completed_at: str | None = None,
    registered_at: str | None = None,
    local_tz: tzinfo | None = None,
) -> ActivityDates:
    """Return the sorted activity dates + basis for one jsonl byte segment.

    Args:
        jsonl_path: the cc session jsonl.
        start: byte offset the segment starts at.
        end: byte offset the segment ends at (exclusive).
        last_completed_at: ISO timestamp of the completed watermark (fallback 1).
        registered_at: ISO timestamp the session was registered (fallback 2).
        local_tz: timezone for the day boundary (None → system local). Injected
            in tests so cross-midnight assertions are deterministic.

    Returns:
        ``ActivityDates`` with sorted unique dates; ``basis`` is
        ``jsonl_timestamp`` when any real event was seen, else the fallback used.
    """
    tz = local_tz or _system_local_tz()
    has_path = bool(str(jsonl_path).strip())
    path = Path(jsonl_path)
    # NOTE: ``Path("")`` is ``.`` (the cwd), so ``exists()`` is True for an empty
    # path — ``is_file()`` correctly returns False for empty paths AND dirs.
    existed = path.is_file()
    dates: set[str] = set()
    if existed and end > max(start, 0):
        try:
            with path.open("rb") as f:
                dates = _scan_range(f, start, end, tz)
        except OSError:
            dates = set()
    if dates:
        return ActivityDates(tuple(sorted(dates)), "jsonl_timestamp")
    if not has_path:
        # no jsonl path recorded at all → no ground truth (the caller then
        # rejects any non-empty diary; production sessions always carry a path).
        return ActivityDates((), "jsonl_timestamp")
    # C-1 fallback chain: a jsonl path WAS recorded but the file is missing, or
    # the segment carried no usable timestamp. Fall back completed_at →
    # registered_at (NEVER the run day).
    for stamp, basis in (
        (last_completed_at, "completed_at"),
        (registered_at, "registered_at"),
    ):
        if stamp:
            day = _parse_iso_to_date(stamp, tz)
            if day:
                return ActivityDates((day,), basis)  # type: ignore[arg-type]
    return ActivityDates((), "jsonl_timestamp")


def _scan_range(f: IO[bytes], start: int, end: int, tz: tzinfo | None) -> set[str]:
    """Scan ``[start, end)`` for activity dates. Drops partial boundary lines."""
    dates: set[str] = set()
    start = max(start, 0)
    f.seek(start)
    if start > 0:
        # is ``start`` at a line boundary, or mid-line? peek the previous byte:
        # a preceding ``\n`` means start is a clean line start (keep it);
        # anything else means start splits a line (that partial is discarded).
        f.seek(start - 1)
        if f.read(1) != b"\n":
            f.seek(start)
            f.readline()  # mid-line → discard the partial first line
        else:
            f.seek(start)
    while True:
        pos = f.tell()
        if pos >= end:
            break
        line = f.readline()
        if not line:
            break
        # a line straddling ``end`` is truncated → skip (next segment's job).
        if f.tell() > end:
            break
        day = _date_of_line(line, tz)
        if day:
            dates.add(day)
    return dates
