"""Read and validate the installed Codex CLI version.

Spec §1 pins the protocol baseline to ``codex-cli 0.144.0``. The transport
refuses to enter ``ready`` when the installed version differs unless the caller
explicitly opts into an override (which still emits a warning — never silent).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from trowel_py.codex_host.errors import VersionMismatchError
from trowel_py.codex_host.protocol import SUPPORTED_CODEX_VERSION

_log = logging.getLogger(__name__)

# ``codex --version`` prints e.g. ``codex-cli 0.144.0``. We keep the leading
# name so a future rename shows up, but compare only the trailing semver.
# A pre-release suffix (``0.144.0-rc.1``) collapses to its release triple —
# intentional, since the installed CLI in this environment is a stable build;
# callers that need to distinguish pre-releases should inspect ``raw``.
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class CodexVersion:
    """The installed Codex CLI version string plus the parsed semver tuple.

    Attributes:
        raw: The full ``codex --version`` output (stripped).
        semver: The ``(major, minor, patch)`` tuple parsed from ``raw``.
    """

    raw: str
    semver: tuple[int, int, int]

    def __str__(self) -> str:
        """Return the bare semver for compact log/UI display."""

        return ".".join(str(part) for part in self.semver)


def parse_version(raw: str) -> CodexVersion:
    """Parse ``codex --version`` output into a :class:`CodexVersion`.

    Args:
        raw: The first line of ``codex --version`` output.

    Returns:
        The parsed version.

    Raises:
        ValueError: If no ``major.minor.patch`` token is present.
    """

    match = _VERSION_RE.search(raw.strip())
    if match is None:
        raise ValueError(f"Could not parse semver from codex --version: {raw!r}")
    parts = tuple(int(p) for p in match.group(1).split("."))
    return CodexVersion(raw=raw.strip(), semver=parts)  # type: ignore[arg-type]


async def read_codex_version(codex_bin: str = "codex") -> CodexVersion:
    """Spawn ``codex --version`` and return the parsed version.

    Does not read config or auth; only the version string is captured.

    Args:
        codex_bin: The codex executable name or path.

    Returns:
        The installed Codex CLI version.

    Raises:
        FileNotFoundError: If the codex binary is not on PATH.
        ValueError: If the version line cannot be parsed.
    """

    proc = await asyncio.create_subprocess_exec(
        codex_bin,
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    return parse_version(stdout.decode().strip())


def check_version(
    installed: CodexVersion,
    *,
    supported: str = SUPPORTED_CODEX_VERSION,
    allow_override: bool = False,
) -> None:
    """Assert the installed version matches the supported baseline.

    Args:
        installed: The version read from the installed CLI.
        supported: The pinned baseline semver (``0.144.0`` by default).
        allow_override: When True, log a warning instead of raising so a
            developer can exercise the transport against an unvalidated build.
            The warning is always emitted — compatibility is never silent
            (spec §1).

    Raises:
        VersionMismatchError: When the versions differ and ``allow_override``
            is False.
    """

    if str(installed) == supported:
        return
    if allow_override:
        _log.warning(
            "Codex version %s differs from validated %s — proceeding because "
            "allow_version_override is set; protocol fields may have drifted.",
            installed,
            supported,
        )
        return
    raise VersionMismatchError(installed=str(installed), supported=supported)
