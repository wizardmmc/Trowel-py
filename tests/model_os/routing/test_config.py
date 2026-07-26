from __future__ import annotations

from pathlib import Path

from trowel_py.cc_host.models import ModelOption
from trowel_py.model_os.routing.config import (
    RoutingConfig,
    load_routing_config,
    validate_routing_config,
)
from trowel_py.model_os.routing.models import RouteCandidate, RouteMode
from trowel_py.model_os.work_broker import ModelTier


def test_missing_routing_config_fails_closed(tmp_path: Path) -> None:
    config = load_routing_config(tmp_path / "missing.toml")

    assert config.mode is RouteMode.OFF
    assert config.runtimes == {}
    assert config.requires_catalog("codex") is False


def test_runtime_candidates_are_validated_against_native_catalogs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[model_os.routing]
mode = "shadow"

[model_os.routing.claude_code.fast]
model = "haiku"
effort = "low"

[model_os.routing.claude_code.deep]
model = "missing-alias"
effort = "high"

[model_os.routing.codex.fast]
model = "gpt-fast"
effort = "low"

[model_os.routing.codex.deep]
model = "gpt-deep"
effort = "unsupported"
""".strip(),
        encoding="utf-8",
    )

    configured = load_routing_config(path)
    validated = validate_routing_config(
        configured,
        cc_catalog=(
            ModelOption("haiku", "Haiku", "real-fast", "", False),
            ModelOption("opus", "Opus", "real-deep", "", False),
        ),
        codex_catalog=(
            {
                "model": "gpt-fast",
                "supported_efforts": ["low", "medium"],
                "default_effort": "medium",
            },
            {
                "model": "gpt-deep",
                "supported_efforts": ["high"],
                "default_effort": "high",
            },
        ),
    )

    assert validated.mode is RouteMode.SHADOW
    assert validated.candidates("claude_code") == (
        validated.candidate("claude_code", ModelTier.FAST),
    )
    assert validated.candidate("claude_code", ModelTier.DEEP) is None
    assert validated.candidate("claude_code", ModelTier.FAST).model == "real-fast"
    assert validated.candidate("claude_code", ModelTier.FAST).request_model == "haiku"
    assert validated.candidate("codex", ModelTier.FAST).model == "gpt-fast"
    assert validated.candidate("codex", ModelTier.DEEP) is None


def test_runtime_without_valid_fast_candidate_is_off(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[model_os.routing]
mode = "canary"

[model_os.routing.codex.deep]
model = "gpt-deep"
effort = "high"
""".strip(),
        encoding="utf-8",
    )

    validated = validate_routing_config(
        load_routing_config(path),
        cc_catalog=(),
        codex_catalog=(
            {
                "model": "gpt-deep",
                "supported_efforts": ["high"],
                "default_effort": "high",
            },
        ),
    )

    assert validated.mode_for("codex") is RouteMode.OFF


def test_off_config_does_not_eagerly_require_native_catalog() -> None:
    config = RoutingConfig(
        RouteMode.OFF,
        {"codex": (RouteCandidate(ModelTier.FAST, "gpt-fast", "low"),)},
    )

    assert config.requires_catalog("codex") is False
