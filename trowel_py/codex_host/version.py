"""读取并校验本机 Codex CLI 版本。

协议基线固定为 ``codex-cli 0.144.0``。版本不一致时 transport 不会进入 ``ready``；
显式 override 可以继续，但仍必须记录警告。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from trowel_py.codex_host.errors import VersionMismatchError
from trowel_py.codex_host.protocol import SUPPORTED_CODEX_VERSION

_log = logging.getLogger(__name__)

# 兼容 ``codex-cli 0.144.0`` 等输出，只用首个 semver 三元组比较；需要区分预发布
# 后缀的调用者应读取 ``raw``。
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class CodexVersion:
    raw: str
    semver: tuple[int, int, int]

    def __str__(self) -> str:
        return ".".join(str(part) for part in self.semver)


def parse_version(raw: str) -> CodexVersion:
    match = _VERSION_RE.search(raw.strip())
    if match is None:
        raise ValueError(f"Could not parse semver from codex --version: {raw!r}")
    parts = tuple(int(p) for p in match.group(1).split("."))
    return CodexVersion(raw=raw.strip(), semver=parts)  # type: ignore[arg-type]


async def read_codex_version(codex_bin: str = "codex") -> CodexVersion:
    """只启动 ``codex --version``，不读取配置或认证信息。"""
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
    """校验基线；``allow_override`` 只将不匹配降为警告，不会静默放行。"""
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
