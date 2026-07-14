"""profile.md domain logic (slice-047): body-section serialize/parse + validate.

This module owns the FIVE-section BODY of ``profile.md`` only — frontmatter IO
(``_split_frontmatter`` / ``_dump_frontmatter``) stays in store.py, so there is
no import cycle (store → profile → types). The store calls
``profile_to_body`` / ``body_to_profile`` / ``validate_profile`` / ``empty_profile``.

Design (grill 2026-07-14, see docs/slices/activate/slice-047.md):
- provenance is structural, not per-field: the body is always user-blessed; AI
  only proposes via a side-channel (→ 050). So Profile is five free-text strings,
  not an item list (an item list would force the 049 UI into a list-editor,
  fighting the chosen 方案 B single-pane document).
- parse is lenient (mirrors ``_core_item_from_dict``): a hand-edited profile.md
  with missing/reordered sections degrades to empty strings, never raises.
- only the five known titles are section boundaries; an unknown ``##`` heading
  (e.g. a user's ``## sub`` inside a section) is kept as BODY TEXT of the current
  section, never dropped (lenient parse, no content loss — ``other`` is the
  catch-all for genuinely new top-level content). The C-4 snapshot backstops any
  rehoming when a programmatic write re-serializes the five sections.
"""
from __future__ import annotations

import re
from typing import get_args

from trowel_py.memory.types import Profile, ProfileSource

#: profile field → markdown ``##`` title. Insertion order is the canonical write order.
_FIELD_TO_TITLE: dict[str, str] = {
    "ability": "能力水平",
    "methodology": "方法论偏好",
    "expression": "表达风格",
    "goal": "长程目标",
    "other": "其他",
}
_TITLE_TO_FIELD: dict[str, str] = {title: field for field, title in _FIELD_TO_TITLE.items()}

#: a level-2 markdown heading line (``## 标题``), allowing trailing whitespace.
_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")

#: allowed write-time source tags (immutability routing). Derived from the
#: ProfileSource Literal so there is a single source of truth.
_VALID_SOURCES: frozenset[str] = frozenset(get_args(ProfileSource))


def empty_profile() -> Profile:
    """The absent-profile sentinel: all dimensions empty, default source.

    Returned by ``store.load_profile`` when profile.md is missing/empty (C-6) so
    callers never deal with None. Cold-start seeding is 050's job, not the store's.
    """
    return Profile()


def profile_to_body(p: Profile) -> str:
    """Render a Profile's five dimensions as the ``## 标题\\n内容`` body.

    Empty dimensions still emit their header (the five sections are always
    present), so the file shape is stable across edits and a human can fill any
    section by hand. Sections are separated by a blank line and the body ends
    with a newline.
    """
    parts: list[str] = []
    for field, title in _FIELD_TO_TITLE.items():
        parts.append(f"## {title}\n{getattr(p, field)}")
    return "\n\n".join(parts) + "\n"


def body_to_profile(body: str, *, updated: str, source: str) -> Profile:
    """Parse a ``## 标题``-sectioned body into a Profile (lenient).

    Missing or reordered sections degrade to empty strings. Only the five KNOWN
    titles are section boundaries — an unknown ``##`` heading (e.g. a hand-added
    ``## sub`` inside a section) is kept as body text of the current section, so
    no content is lost (``other`` is the catch-all for genuinely new top-level
    content). ``updated``/``source`` come from frontmatter (passed in by the
    store); they are not encoded in the body.
    """
    dims: dict[str, list[str]] = {field: [] for field in _FIELD_TO_TITLE}
    current: str | None = None
    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m and (field := _TITLE_TO_FIELD.get(m.group(1).strip())) is not None:
            # a KNOWN title starts a new section; an unknown `##` line falls
            # through and is kept as body text of the current section.
            current = field
            continue
        if current is not None:
            dims[current].append(line)
    kwargs: dict[str, str] = {field: "\n".join(lines).strip() for field, lines in dims.items()}
    return Profile(updated=updated, source=source, **kwargs)


def validate_profile(p: Profile, source: str) -> None:
    """Raise ValueError unless the Profile is write-valid and source is tagged.

    Checks the five dimensions are strings, ``updated`` is a non-empty ISO date
    (C-3), and ``source`` is one of the allowed write-path tags. The runtime type
    check is needed because frozen dataclasses do not enforce field types, and a
    caller could build ``Profile(ability=123, ...)``.
    """
    errors: list[str] = []
    for field in _FIELD_TO_TITLE:
        if not isinstance(getattr(p, field), str):
            errors.append(f"profile: '{field}' must be a string")
    if not isinstance(p.updated, str) or not p.updated.strip():
        errors.append("profile: 'updated' is required (ISO date)")
    if source not in _VALID_SOURCES:
        errors.append(
            f"profile: 'source' must be one of {sorted(_VALID_SOURCES)}, got {source!r}"
        )
    if errors:
        raise ValueError(f"invalid profile: {errors}")
