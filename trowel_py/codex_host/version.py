"""读取并校验本机 Codex CLI 版本。

版本校验使用 ``SUPPORTED_CODEX_VERSION`` 指定的协议基线。不匹配时默认抛出异常；
显式 override 允许调用方继续，但仍会记录协议可能漂移的警告。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from trowel_py.codex_host.errors import VersionMismatchError
from trowel_py.codex_host.protocol import SUPPORTED_CODEX_VERSION

_log = logging.getLogger(__name__)

# 从 ``codex-cli 0.144.0`` 等输出中只取首个三段数字；预发布和构建后缀不参与
# 兼容性比较，需要完整版本文本时读取 ``raw``。
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


@dataclass(frozen=True)
class CodexVersion:
    """保存 Codex CLI 的完整版本文本和用于兼容性比较的数字版本。

    Attributes:
        raw: ``codex --version`` 的完整文本；``parse_version()`` 会去除首尾空白。
        semver: 从输出中首个三段数字解析出的整数元组。
    """

    raw: str
    semver: tuple[int, int, int]

    def __str__(self) -> str:
        """返回不含 CLI 名称或版本后缀的三段数字版本。"""

        return ".".join(str(part) for part in self.semver)


def parse_version(raw: str) -> CodexVersion:
    """从 ``codex --version`` 输出中提取首个三段数字版本。

    预发布或构建后缀保留在 ``raw`` 中，但不进入 ``semver``。

    Args:
        raw: CLI 输出的完整版本文本。

    Returns:
        去除首尾空白的原文及解析后的数字版本。

    Raises:
        ValueError: 输出中没有三段数字版本。
    """

    match = _VERSION_RE.search(raw.strip())
    if match is None:
        raise ValueError(f"Could not parse semver from codex --version: {raw!r}")
    parts = tuple(int(p) for p in match.group(1).split("."))
    # 正则固定捕获三段数字，类型检查器无法从 split 推断 tuple 长度。
    return CodexVersion(raw=raw.strip(), semver=parts)  # type: ignore[arg-type]


async def read_codex_version(codex_bin: str = "codex") -> CodexVersion:
    """执行 ``<codex_bin> --version`` 并解析 stdout。

    stderr 会被丢弃，也不会单独检查退出码；命令无法启动或 stdout 无法解析时，
    底层异常直接向上传播。

    Args:
        codex_bin: Codex CLI 可执行文件名或路径。

    Returns:
        CLI 报告的完整版本文本和数字版本。
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
    """比较已安装版本与协议基线，并拒绝未获准的不匹配。

    ``allow_override`` 只把不匹配降为警告，不会静默放行。

    Args:
        installed: 从本机 CLI 读取的版本。
        supported: Trowel 已验证的三段数字版本。
        allow_override: 是否在记录警告后允许不匹配版本继续运行。

    Raises:
        VersionMismatchError: 版本不匹配且未启用 override。
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
