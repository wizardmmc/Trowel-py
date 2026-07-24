import json
from pathlib import Path

from trowel_py.cc_host.models import ModelOption, list_models


def _write_settings(path: Path, env: dict) -> None:
    path.write_text(json.dumps({"env": env}), encoding="utf-8")


class TestListModels:
    def test_reads_alias_mapping_in_capability_order(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(
            s,
            {
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                "ANTHROPIC_MODEL": "glm-5.2",
            },
        )
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
        assert opus.description

    def test_only_configured_aliases_returned(self, tmp_path: Path):
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
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_DEFAULT_OPUS_MODEL": 123,
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                    }
                }
            ),
            encoding="utf-8",
        )
        out = list_models(s)
        assert [m.value for m in out] == ["sonnet"]

    def test_settings_without_env_block_returns_empty(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        s.write_text(json.dumps({"model": "sonnet"}), encoding="utf-8")
        assert list_models(s) == []

    def test_non_dict_env_block_returns_empty(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        s.write_text(json.dumps({"env": "not-a-dict"}), encoding="utf-8")
        assert list_models(s) == []


class TestIsDefault:
    def test_anthropic_model_matching_sonnet_real_marks_sonnet(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(
            s,
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
                "ANTHROPIC_MODEL": "glm-5.1",
            },
        )
        out = {m.value: m.is_default for m in list_models(s)}
        assert out == {"opus": False, "sonnet": True, "haiku": False}

    def test_no_anthropic_model_falls_back_to_sonnet(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(
            s,
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
            },
        )
        out = {m.value: m.is_default for m in list_models(s)}
        assert out["sonnet"] is True
        assert out["opus"] is False

    def test_anthropic_model_matching_opus_marks_opus(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(
            s,
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                "ANTHROPIC_MODEL": "glm-5.2[1M]",
            },
        )
        out = {m.value: m.is_default for m in list_models(s)}
        assert out["opus"] is True
        assert out["sonnet"] is False

    def test_no_sonnet_in_list_falls_back_to_first(self, tmp_path: Path):
        s = tmp_path / "settings.json"
        _write_settings(
            s,
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.1",
            },
        )
        out = {m.value: m.is_default for m in list_models(s)}
        assert out == {"opus": True, "haiku": False}

    def test_anthropic_model_matching_no_alias_falls_back_to_sonnet(
        self, tmp_path: Path
    ):
        s = tmp_path / "settings.json"
        _write_settings(
            s,
            {
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2[1M]",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1",
                "ANTHROPIC_MODEL": "some-unknown-model",
            },
        )
        out = {m.value: m.is_default for m in list_models(s)}
        assert out["sonnet"] is True
