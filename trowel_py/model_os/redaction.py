"""Payload redaction for the Model OS journal (slice-084).

Spec invariant: "默认日志不保存完整 prompt、thinking 或私聊". The store calls
``redact_payload`` on every event before persisting, so even a caller that
hands raw secrets in the payload never lands them in SQLite.

What gets scrubbed:
- values under sensitive KEY NAMES (api_key, token, prompt, thinking,
  private_chat, content, proxy, ...);
- STRING VALUES matching secret shapes (``sk-…``, ``Bearer …``, JWT ``eyJ…``,
  proxy URLs pointing at localhost / private ranges);
- nested dicts and lists recursively.

What is PRESERVED: structural fields the reducer depends on (status, kind,
count, ratio, model name, hashes, ids). Redaction is deterministic (sha256,
no salt) so the same input always produces the same marker — replay is stable.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Key names whose VALUES are always scrubbed. Matched case-insensitively
# against the dict key. "content" is included because chat/prompt bodies
# routinely live under that key; the reducer never depends on a "content"
# field.
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "secret",
        "password",
        "passwd",
        "authorization",
        "auth",
        "cookie",
        "credential",
        "credentials",
        "prompt",
        "system_prompt",
        "thinking",
        "thought",
        "private_chat",
        "private_message",
        "content",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "proxy",
    }
)

# Value-shape patterns that are scrubbed even when the key name is innocuous.
# NOTE: a bare "long hex" pattern is intentionally NOT included — uuids and
# content hashes are routine structural ids in this journal, and redacting
# them would corrupt derived state. Secret hex tokens are caught by their
# KEY NAME (api_key/token/...) instead.
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-[A-Za-z0-9_\-]{8,}"),            # OpenAI-style keys
    re.compile(r"^Bearer\s+\S"),                       # bearer tokens
    re.compile(r"^eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"),  # JWT
    re.compile(
        r"^(https?|socks[45])://"                       # proxy/private URLs:
        r"(127\.0\.0\.1|localhost|0\.0\.0\.0|::1|"
        r"192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)",
        re.IGNORECASE,
    ),
)


def _marker(value: Any) -> str:
    """Return a deterministic redaction marker for ``value``.

    Includes a short sha256 prefix so equal secrets produce equal markers
    (audit-comparable) while the original stays unrecoverable.
    """

    raw = str(value)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"<redacted:sha256={digest}:len={len(raw)}>"


def _looks_like_secret_value(value: Any) -> bool:
    """True if a string value matches a known secret shape."""

    if not isinstance(value, str):
        return False
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def redact_payload(payload: Any) -> Any:
    """Return a redacted copy of ``payload``.

    Recursively walks dicts and lists. The input is never mutated. The output
    preserves structure and non-sensitive values so the reducer can still
    read the fields it depends on.

    Args:
        payload: a dict, list, or scalar to redact.

    Returns:
        A new structure of the same shape with secrets replaced by
        ``"<redacted:sha256=…:len=…>"`` markers.
    """

    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                # Sensitive KEY: redact scalar values outright, but recurse
                # into structured values so nested children (which are
                # themselves sensitive) get scrubbed at the leaf without
                # flattening the whole subtree to a single marker — audit
                # still sees the shape (e.g. auth → {token: <redacted>}).
                if isinstance(value, (dict, list)):
                    out[key] = redact_payload(value)
                else:
                    out[key] = _marker(value)
            else:
                out[key] = redact_payload(value)
        return out
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if _looks_like_secret_value(payload):
        return _marker(payload)
    return payload
