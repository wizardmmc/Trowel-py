from __future__ import annotations

from trowel_py.codex_host.secrets import (
    redact_env,
    redact_message,
    redact_stderr,
    redact_value,
)


def test_redact_value_replaces_token_keys() -> None:
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
    original = {"auth_token": "abc123"}
    redact_value(original)
    assert original == {"auth_token": "abc123"}


def test_redact_value_scrubs_url_userinfo() -> None:
    """RFC 3986 要求 URL password 中的 ``@`` 编码，因此终止符没有歧义。"""

    assert (
        redact_value("https://bot:passw0rd@proxy.example.com:7897")
        == "https://bot:***@proxy.example.com:7897"
    )


def test_redact_value_scrubs_bearer_and_key_prefixes() -> None:
    assert (
        redact_value("Authorization: Bearer eyJabc.def.ghi")
        == "Authorization: Bearer ***"
    )
    assert redact_value("key=sk-1234567890abcdef") == "key=sk-***"


def test_redact_value_recurses_into_nested_structures() -> None:
    out = redact_value(
        {"params": {"config": {"env": {"OPENAI_API_KEY": "sk-deadbeef"}}}}
    )
    assert out["params"]["config"]["env"]["OPENAI_API_KEY"] == "***REDACTED***"


def test_redact_message_keeps_protocol_shape() -> None:
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
    assert (
        redact_stderr("failed with token=sk-1234567890abcdef here")
        == "failed with token=sk-*** here"
    )
    assert (
        redact_stderr("via http://bot:secret@proxy:7897")
        == "via http://bot:***@proxy:7897"
    )
