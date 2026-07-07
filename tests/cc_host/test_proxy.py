"""Tests for cc_host.proxy — slice-030 本地反代。

Round 1 (this file): pure functions — load_settings_env / build_proxy_env /
replace_system_identity / should_replace. Route + streaming tests are added
in a later RED cycle once the pure logic is green.
"""
import json
from pathlib import Path

import pytest

from trowel_py.cc_host.proxy import (
    TUI_SYSTEM_IDENTITY,
    build_proxy_env,
    load_settings_env,
    replace_system_identity,
    should_replace,
)


# ---------- load_settings_env ----------


class TestLoadSettingsEnv:
    def test_reads_env_block(self, tmp_path: Path):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
                        "ANTHROPIC_AUTH_TOKEN": "tok-123",
                        "ANTHROPIC_MODEL": "glm-5.1",
                        "OTHER_VAR": "x",
                    },
                    "model": "glm-5.1",
                }
            )
        )
        env = load_settings_env(settings)
        assert env["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "tok-123"
        assert env["ANTHROPIC_MODEL"] == "glm-5.1"
        assert env["OTHER_VAR"] == "x"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert load_settings_env(tmp_path / "nope.json") == {}

    def test_no_env_block_returns_empty(self, tmp_path: Path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"model": "glm-5.1"}))
        assert load_settings_env(settings) == {}

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        settings = tmp_path / "settings.json"
        settings.write_text("{not json")
        assert load_settings_env(settings) == {}


# ---------- build_proxy_env ----------


class TestBuildProxyEnv:
    def test_sets_provider_managed_flag(self):
        env = build_proxy_env({}, "http://127.0.0.1:8000")
        assert env["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] == "1"

    def test_replaces_base_url_with_proxy(self):
        settings_env = {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"}
        env = build_proxy_env(settings_env, "http://127.0.0.1:8000")
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8000"

    def test_passes_through_token_and_model(self):
        settings_env = {
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "tok-xyz",
            "ANTHROPIC_MODEL": "glm-5.1",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2",
        }
        env = build_proxy_env(settings_env, "http://127.0.0.1:8000")
        assert env["ANTHROPIC_AUTH_TOKEN"] == "tok-xyz"
        assert env["ANTHROPIC_MODEL"] == "glm-5.1"
        assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "glm-5.2"

    def test_returns_only_increment_not_os_environ(self):
        """build_proxy_env returns only the proxy delta; merging os.environ is
        the caller's job (launcher/CCHost). Keeps this function pure + testable."""
        import os

        os.environ["TROWEL_TEST_PATHOLOGICAL"] = "should-not-leak"
        try:
            env = build_proxy_env({}, "http://127.0.0.1:8000")
            assert "TROWEL_TEST_PATHOLOGICAL" not in env
        finally:
            del os.environ["TROWEL_TEST_PATHOLOGICAL"]


# ---------- replace_system_identity ----------


def _p_body() -> dict:
    """A representative -p body: system has billing header + Claude-agent
    identity block + detail prompt. Mirrors what CC -p actually sends."""
    return {
        "model": "glm-5.1",
        "system": [
            {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.197.123; cc_entrypoint=sdk-cli;"},
            {
                "type": "text",
                "text": "You are a Claude agent, built on Anthropic...",
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": "<详细 prompt>"},
        ],
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "Bash"}],
    }


class TestReplaceSystemIdentity:
    def test_replaces_identity_block_text(self):
        out = replace_system_identity(_p_body())
        # billing header dropped -> identity is now system[0]
        assert out["system"][0]["text"] == TUI_SYSTEM_IDENTITY

    def test_preserves_cache_control(self):
        out = replace_system_identity(_p_body())
        assert out["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_drops_billing_header_block(self):
        """The -p billing-header block must be dropped (TUI has none; 智谱
        caches by the whole system array). Pinned by slice-030 V5/V6."""
        out = replace_system_identity(_p_body())
        assert len(out["system"]) == 2  # was 3, billing header dropped
        texts = [b["text"] for b in out["system"]]
        assert not any(t.startswith("x-anthropic-billing-header") for t in texts)

    def test_preserves_tools_messages_and_other_system_blocks(self):
        body = _p_body()
        out = replace_system_identity(body)
        assert out["tools"] == body["tools"]
        assert out["messages"] == body["messages"]
        # billing header dropped -> identity now [0], detail prompt now [1]
        assert out["system"][0]["text"] == TUI_SYSTEM_IDENTITY
        assert out["system"][1]["text"] == "<详细 prompt>"

    def test_original_body_not_mutated(self):
        body = _p_body()
        original_len = len(body["system"])
        original_text = body["system"][1]["text"]
        _ = replace_system_identity(body)
        assert body["system"][1]["text"] == original_text
        assert len(body["system"]) == original_len  # billing drop is on the copy

    def test_matches_identity_block_at_any_index(self):
        """身份块不在固定索引（CC 不同请求 system 结构可能变）——按内容匹配，
        不写死 [1]。"""
        body = {
            "system": [
                {"type": "text", "text": "You are a Claude agent, built on Anthropic..."},
                {"type": "text", "text": "<other>"},
            ]
        }
        out = replace_system_identity(body)
        assert out["system"][0]["text"] == TUI_SYSTEM_IDENTITY
        assert out["system"][1]["text"] == "<other>"

    def test_drops_billing_header_even_when_not_first(self):
        """slice-030 V5 pinned: the billing header spoils the cache even when
        moved off index 0, so it must be dropped regardless of position."""
        body = {
            "system": [
                {"type": "text", "text": "You are a Claude agent, built on Anthropic..."},
                {"type": "text", "text": "x-anthropic-billing-header: cc_version=2.1.197;"},
                {"type": "text", "text": "<detail>"},
            ]
        }
        out = replace_system_identity(body)
        texts = [b["text"] for b in out["system"]]
        assert not any(t.startswith("x-anthropic-billing-header") for t in texts)
        assert out["system"][0]["text"] == TUI_SYSTEM_IDENTITY

    def test_idempotent_if_already_tui(self):
        body = {"system": [{"type": "text", "text": TUI_SYSTEM_IDENTITY}]}
        out = replace_system_identity(body)
        assert out["system"][0]["text"] == TUI_SYSTEM_IDENTITY

    def test_no_identity_block_returns_equal_body(self):
        """找不到身份块 → 降级原样（不阻断，spec Q6 兜底）。"""
        body = {"system": [{"type": "text", "text": "<no identity here>"}]}
        out = replace_system_identity(body)
        assert out == body

    def test_system_is_string_returns_equal_body(self):
        body = {"system": "You are a helpful assistant."}
        out = replace_system_identity(body)
        assert out == body

    def test_no_system_key_returns_equal_body(self):
        body = {"messages": []}
        out = replace_system_identity(body)
        assert out == body


# ---------- should_replace ----------


class TestShouldReplace:
    def test_bigmodel(self):
        assert should_replace("https://open.bigmodel.cn/api/anthropic") is True

    def test_official_anthropic(self):
        assert should_replace("https://api.anthropic.com") is False

    def test_other_provider(self):
        assert should_replace("https://api.openai.com") is False

    def test_empty(self):
        assert should_replace("") is False
