from __future__ import annotations

import pytest

from trowel_py.codex_host.errors import VersionMismatchError
from trowel_py.codex_host.version import (
    CodexVersion,
    check_version,
    parse_version,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("codex-cli 0.144.0", (0, 144, 0)),
        ("codex-cli 1.2.3", (1, 2, 3)),
        ("0.10.0-rc.1 extra", (0, 10, 0)),
        ("  codex 0.0.1\n", (0, 0, 1)),
    ],
)
def test_parse_version_extracts_semver(
    raw: str, expected: tuple[int, int, int]
) -> None:
    parsed = parse_version(raw)
    assert parsed.semver == expected


def test_parse_version_raises_on_missing_semver() -> None:
    with pytest.raises(ValueError):
        parse_version("codex-cli unknown")


def test_version_str_is_bare_semver() -> None:
    assert str(CodexVersion("codex-cli 0.144.0", (0, 144, 0))) == "0.144.0"


def test_check_version_passes_on_supported() -> None:
    check_version(CodexVersion("codex-cli 0.144.0", (0, 144, 0)))


def test_check_version_raises_on_mismatch() -> None:
    installed = CodexVersion("codex-cli 0.150.0", (0, 150, 0))
    with pytest.raises(VersionMismatchError) as exc:
        check_version(installed)
    assert exc.value.installed == "0.150.0"
    assert exc.value.supported == "0.144.0"


def test_check_version_override_logs_warning_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    installed = CodexVersion("codex-cli 0.999.0", (0, 999, 0))
    with caplog.at_level("WARNING", logger="trowel_py.codex_host.version"):
        check_version(installed, allow_override=True)
    assert any(
        "0.999.0" in rec.message and "0.144.0" in rec.message for rec in caplog.records
    )
