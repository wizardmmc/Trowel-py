"""Tests for cc_host.models — reads cc's ~/.claude/settings.json env to list
the alias → real-model mapping for the /model picker.

slice-027 C2: cc uses fixed aliases (sonnet/opus/haiku, aliases.ts) mapped to
real models via ANTHROPIC_DEFAULT_<ALIAS>_MODEL env. trowel surfaces those
aliases (which work across backends) instead of hardcoding GLM ids.
"""
import json
from pathlib import Path

import pytest

from trowel_py.cc_host.models import ModelOption, list_models


def _write_settings(path: Path, env: dict) -> None:
    path.write_text(json.dumps({"env": env}), encoding="utf-8")


class TestListModels:
    def test_reads_alias_mapping_in_capability_order(self, tmp_path: Path):
        """opus first (strongest), then sonnet, then haiku — cc's ordering."""
        s = tmp_path / "settings.json"
        _write_settings(s, {
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
            "ANTHROPIC_MODEL": "glm-5.2",  # fallback, not an alias — ignored
        })
        out = list_models(s)
        assert [m.value for m in out] == ["opus", "sonnet", "haiku"]

    def test_alias_carries_real_model_and_label(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(s, {"ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]"})
        opus = list_models(s)[0]
        assert isinstance(opus, ModelOption)
        assert opus.value == "opus"
        assert opus.label == "Opus"
        assert opus.real_model == "glm-5.2[1M]"
        assert opus.description  # non-empty subtitle

    def test_only_configured_aliases_returned(self, tmp_path: Path):
        """A backend mapping only sonnet shows only sonnet — no guessing."""
        s = tmp_path / "settings.json"
        _write_settings(s, {"ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1"})
        out = list_models(s)
        assert [m.value for m in out] == ["sonnet"]

    def test_missing_settings_file_returns_empty(self, tmp_path: Path):
        assert list_models(tmp_path / "nope.json") == []

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        s.write_text("{not valid json", encoding="utf-8")
        assert list_models(s) == []

    def test_non_string_env_values_skipped(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        s.write_text(
            json.dumps({"env": {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": 123,  # non-string → skip
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
            }}),
            encoding="utf-8",
        )
        out = list_models(s)
        assert [m.value for m in out] == ["sonnet"]

    def test_settings_without_env_block_returns_empty(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        s.write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
        assert list_models(s) == []

    def test_non_dict_env_block_returns_empty(self, tmp_path: Path):
        """A malformed settings.json where 'env' isn't an object must not crash
        the picker — return empty and let the frontend fall back to cc init."""
        s = tmp_path / "settings.json"
        s.write_text(json.dumps({"env": "not-a-dict"}), encoding="utf-8")
        assert list_models(s) == []


class TestIsDefault:
    """slice-034 feat 3: is_default 标记 cc 不显式设 model 时的回退 alias，
    供前端 chip 显示"默认回退"值。规则：ANTHROPIC_MODEL 匹配某 alias 的
    real_model → 该 alias 默认；未设/匹配不上 → sonnet（不在列表则首个）。"""

    def test_anthropic_model_matching_sonnet_real_marks_sonnet(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(s, {
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
            "ANTHROPIC_MODEL": "glm-5.1",  # == sonnet real
        })
        out = {m.value: m.is_default for m in list_models(s)}
        assert out == {"opus": False, "sonnet": True, "haiku": False}

    def test_no_anthropic_model_falls_back_to_sonnet(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(s, {
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
        })
        out = {m.value: m.is_default for m in list_models(s)}
        assert out["sonnet"] is True
        assert out["opus"] is False

    def test_anthropic_model_matching_opus_marks_opus(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(s, {
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
            "ANTHROPIC_MODEL": "glm-5.2[1M]",  # == opus real
        })
        out = {m.value: m.is_default for m in list_models(s)}
        assert out["opus"] is True
        assert out["sonnet"] is False

    def test_no_sonnet_in_list_falls_back_to_first(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(s, {
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
        })
        out = {m.value: m.is_default for m in list_models(s)}
        assert out == {"opus": True, "haiku": False}

    def test_anthropic_model_matching_no_alias_falls_back_to_sonnet(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(s, {
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
            "ANTHROPIC_MODEL": "some-unknown-model",  # matches nothing
        })
        out = {m.value: m.is_default for m in list_models(s)}
        assert out["sonnet"] is True
