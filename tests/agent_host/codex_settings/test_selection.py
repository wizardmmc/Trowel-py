from __future__ import annotations

import pytest

from trowel_py.agent_host.codex_settings import (
    NoUsableEffortError,
    UnknownModelError,
    select_turn_settings,
)


def test_turn_settings_follow_existing_precedence() -> None:
    selected = select_turn_settings(
        [
            {
                "id": "native-default",
                "model": "native-default-alias",
                "is_default": True,
                "default_effort": "medium",
                "supported_efforts": [{"value": "medium"}, {"value": "high"}],
            },
            {
                "id": "requested",
                "model": "requested-alias",
                "is_default": False,
                "default_effort": "high",
                "supported_efforts": [{"value": "high"}, {"value": "ultra"}],
            },
        ],
        requested_model="requested-alias",
        stored_model="stored",
        native_model="native",
        configured_model="configured",
        requested_effort="ultra",
        stored_effort="high",
        native_effort="medium",
        configured_effort="low",
    )

    assert selected.model == "requested"
    assert selected.effort == "ultra"
    assert selected.adjusted is False


def test_turn_settings_use_native_default_and_adjust_unsupported_effort() -> None:
    selected = select_turn_settings(
        [
            {
                "id": "first",
                "model": "first-alias",
                "is_default": False,
                "default_effort": "low",
                "supported_efforts": [{"value": "low"}],
            },
            {
                "id": "default",
                "model": "default-alias",
                "is_default": True,
                "default_effort": "medium",
                "supported_efforts": [{"value": "medium"}, {"value": "high"}],
            },
        ],
        requested_model=None,
        stored_model=None,
        native_model=None,
        configured_model=None,
        requested_effort="ultra",
        stored_effort=None,
        native_effort=None,
        configured_effort=None,
    )

    assert selected.model == "default"
    assert selected.effort == "medium"
    assert selected.adjusted is True


@pytest.mark.parametrize(
    ("model_sources", "expected"),
    [
        (("a", "b", "c", "d"), "a"),
        ((None, "b", "c", "d"), "b"),
        ((None, None, "c", "d"), "c"),
        ((None, None, None, "d"), "d"),
        (("", None, None, None), "a"),
    ],
)
def test_turn_settings_model_source_precedence(
    model_sources: tuple[str | None, str | None, str | None, str | None],
    expected: str,
) -> None:
    catalog = [
        {
            "id": model,
            "model": model,
            "is_default": model == "a",
            "default_effort": "medium",
            "supported_efforts": [{"value": "medium"}],
        }
        for model in ("a", "b", "c", "d")
    ]

    selected = select_turn_settings(
        catalog,
        requested_model=model_sources[0],
        stored_model=model_sources[1],
        native_model=model_sources[2],
        configured_model=model_sources[3],
        requested_effort=None,
        stored_effort=None,
        native_effort=None,
        configured_effort=None,
    )

    assert selected.model == expected
    assert selected.effort == "medium"
    assert selected.adjusted is False


def test_turn_settings_reject_unknown_model() -> None:
    with pytest.raises(UnknownModelError, match="missing"):
        select_turn_settings(
            [],
            requested_model="missing",
            stored_model=None,
            native_model=None,
            configured_model=None,
            requested_effort=None,
            stored_effort=None,
            native_effort=None,
            configured_effort=None,
        )


def test_turn_settings_reject_model_without_usable_default_effort() -> None:
    with pytest.raises(NoUsableEffortError, match="model-a"):
        select_turn_settings(
            [
                {
                    "id": "model-a",
                    "model": "model-a",
                    "is_default": True,
                    "default_effort": "medium",
                    "supported_efforts": [],
                }
            ],
            requested_model=None,
            stored_model=None,
            native_model=None,
            configured_model=None,
            requested_effort=None,
            stored_effort=None,
            native_effort=None,
            configured_effort=None,
        )
