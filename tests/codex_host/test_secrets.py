"""Redaction tests — auth, tokens, credential-bearing proxy strings never land.

Spec C-6: secrets must be scrubbed before any line reaches a fixture or log.
These cases pin the patterns we care about so a regression that echoes raw
auth is caught at the unit layer.
"""

from __future__ import annotations

from trowel_py.codex_host.secrets import (
    redact_env,
    redact_message,
    redact_stderr,
    redact_value,
)


def test_redact_value_replaces_token_keys() -> None:
    """Any key matching token/auth/secret/api_key blanks the value."""

    out = redact_value(
        {
            "auth_token": "abc123",
            "API_KEY": "sk-live",
            "authorizationHeader": "Bearer xyz",
            "safe_field": "keep-me",
        }
    )
    assert out["auth_token"] == "***REDACTED***"
    assert out["API_KEY"] == "***REDACTED***"
    assert out["authorizationHeader"] == "***REDACTED***"
    assert out["safe_field"] == "keep-me"


def test_redact_value_does_not_mutate_input() -> None:
    """Immutability: the original dict is untouched."""

    original = {"auth_token": "abc123"}
    redact_value(original)
    assert original == {"auth_token": "abc123"}


def test_redact_value_scrubs_url_userinfo() -> None:
    """``scheme://user:pass@host`` loses only the password.

    Per RFC 3986 a URL password containing ``@`` must be percent-encoded, so
    the userinfo terminator is unambiguous — we test the well-formed case.
    """

    assert (
        redact_value("https://bot:passw0rd@proxy.example.com:7897")
        == "https://bot:***@proxy.example.com:7897"
    )


def test_redact_value_scrubs_bearer_and_key_prefixes() -> None:
    """Bearer tokens and ``sk-`` keys are masked inline."""

    assert redact_value("Authorization: Bearer eyJabc.def.ghi") == "Authorization: Bearer ***"
    assert redact_value("key=sk-1234567890abcdef") == "key=sk-***"


def test_redact_value_recurses_into_nested_structures() -> None:
    """Secrets deep inside params/config get scrubbed too."""

    out = redact_value(
        {"params": {"config": {"env": {"OPENAI_API_KEY": "sk-deadbeef"}}}}
    )
    assert out["params"]["config"]["env"]["OPENAI_API_KEY"] == "***REDACTED***"


def test_redact_message_keeps_protocol_shape() -> None:
    """A realistic notification keeps every field except scrubbed secrets."""

    msg = {
        "method": "item/completed",
        "params": {
            "threadId": "tid",
            "item": {"type": "commandExecution", "aggregatedOutput": "ok"},
            "config": {"env": {"OPENAI_API_KEY": "sk-deadbeef"}},
        },
    }
    redacted = redact_message(msg)
    assert redacted["method"] == "item/completed"
    assert redacted["params"]["item"]["aggregatedOutput"] == "ok"
    assert redacted["params"]["config"]["env"]["OPENAI_API_KEY"] == "***REDACTED***"
    assert msg["params"]["config"]["env"]["OPENAI_API_KEY"] == "sk-deadbeef"


def test_redact_env_blanks_secret_keys_only() -> None:
    """Env mirror hides secret keys and copies the rest verbatim."""

    env = {
        "PATH": "/usr/bin",
        "HTTPS_PROXY": "http://u:p@127.0.0.1:7897",
        "HOME": "/tmp/x",
        "OPENAI_API_KEY": "sk-xyz",
    }
    out = redact_env(env)
    assert out["PATH"] == "/usr/bin"
    assert out["HOME"] == "/tmp/x"
    assert out["HTTPS_PROXY"] == "***REDACTED***"
    assert out["OPENAI_API_KEY"] == "***REDACTED***"


def test_redact_stderr_strips_inline_credentials() -> None:
    """stderr excerpts that reach exceptions must not carry tokens."""

    assert (
        redact_stderr("failed with token=sk-1234567890abcdef here")
        == "failed with token=sk-*** here"
    )
    assert (
        redact_stderr("via http://bot:secret@proxy:7897")
        == "via http://bot:***@proxy:7897"
    )
