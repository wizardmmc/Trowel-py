"""Secret / environment redaction for diagnostics and raw recordings.

Spec C-6: auth, tokens, credential-bearing proxy strings and full env must
never enter logs or fixtures. The recorder calls :func:`redact_message` on
every line before it touches disk; the transport uses :func:`redact_stderr`
on stderr excerpts that bubble up into exceptions.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# Keys whose values are scrubbed wholesale when mirroring an env dict or
# echoing a config object into a diagnostic. Matching is case-insensitive
# (HTTP_PROXY / http_proxy / HttpProxy all treated the same).
_SECRET_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r".*token.*",
        r".*secret.*",
        r".*password.*",
        r".*passwd.*",
        r".*api[_-]?key.*",
        r".*auth.*",
        r".*credential.*",
        r".*bearer.*",
        r".*cookie.*",
        r".*proxy.*",  # proxy URLs may embed user:pass@
    )
)

# Inline credential patterns inside otherwise-free-form strings (URLs with
# userinfo, ``Bearer xxx``, common key prefixes). Each is replaced by a short
# placeholder that keeps the structure visible without leaking the value.
_INLINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ``scheme://user:pass@host`` → ``scheme://user:***@host`` (pass dropped,
    # never echoed back). Real URL passwords must be percent-encoded so the
    # userinfo terminator ``@`` is unambiguous — we do not try to salvage a
    # password that itself contains a bare ``@``.
    (re.compile(r"(://[^:/@\s]+):([^@/\s]+)@"), r"\1:***@"),
    # ``Bearer xxx`` / ``bearer xxx`` → ``Bearer ***``
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 ***"),
    # ``sk-...`` style long-lived keys → ``sk-***``
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    # ``eyJ...`` JWT-like compact blobs (header.payload.sig) → ``eyJ***``
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), "eyJ***"),
)

_REDACTED = "***REDACTED***"


def _looks_secret(key: str) -> bool:
    """Return True if an env/config key name matches a known secret pattern."""

    return any(pattern.match(key) for pattern in _SECRET_KEY_PATTERNS)


def redact_value(value: Any) -> Any:
    """Redact one scalar/string value, recursing into containers.

    Args:
        value: The parsed JSON value to scrub.

    Returns:
        A new value of the same shape with secrets replaced. Original input is
        never mutated (immutable: new containers are built).
    """

    if isinstance(value, Mapping):
        return {k: (_REDACTED if _looks_secret(str(k)) else redact_value(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(text: str) -> str:
    """Apply every inline credential pattern to a free-form string."""

    redacted = text
    for pattern, replacement in _INLINE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_message(message: Any) -> Any:
    """Redact a parsed JSON-RPC message before it is written to disk.

    Thin alias of :func:`redact_value` named for its call site (the recorder).
    The original object is untouched; a scrubbed deep copy is returned.

    Args:
        message: The parsed message object.

    Returns:
        A scrubbed copy safe to persist.
    """

    return redact_value(message)


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of an env mapping with secret keys blanked.

    Used when the recorder wants to note *which* proxy env vars were set
    without ever echoing their values.

    Args:
        env: The subprocess environment mapping (e.g. ``os.environ``).

    Returns:
        A new dict where secret keys map to ``"***REDACTED***"`` and the rest
        are copied verbatim.
    """

    return {key: (_REDACTED if _looks_secret(key) else value) for key, value in env.items()}


def redact_stderr(text: str) -> str:
    """Scrub a stderr excerpt for use in an exception message.

    Args:
        text: A chunk of app-server stderr.

    Returns:
        The same chunk with inline credentials stripped.
    """

    return _redact_string(text)
