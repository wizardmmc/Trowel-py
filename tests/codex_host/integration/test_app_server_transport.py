"""真实 Codex app-server 的 Transport smoke。

测试默认由 integration marker 筛除，且只在 ``CODEX_INTEGRATION=1`` 时运行；测试
代码不直接读取认证文件，子进程继承当前环境和 Codex 登录。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from trowel_py.codex_host import AppServerClient

_ENV_GATE = "CODEX_INTEGRATION"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get(_ENV_GATE) != "1",
        reason=f"set {_ENV_GATE}=1 to run the real Codex app-server smoke",
    ),
]


async def test_real_initialize_and_thread_start() -> None:
    """空 thread 没有 rollout，0.144.0 拒绝 archive；使用 ephemeral 避免残留。"""

    workdir = Path(tempfile.mkdtemp(prefix="trowel-codex-integration-"))
    try:
        async with _client(workdir) as client:
            assert client.version is not None
            assert str(client.version) == "0.144.0"
            assert client.initialize_result is not None
            assert "userAgent" in client.initialize_result

            started = await client.request(
                "thread/start",
                {
                    "cwd": str(workdir),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": True,
                },
                timeout=30,
            )
            thread_id = started["thread"]["id"]
            assert isinstance(thread_id, str) and thread_id
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)


class _AsyncCtx:
    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._client: AppServerClient | None = None

    async def __aenter__(self) -> AppServerClient:
        """不覆盖 env，避免用局部字典替换 PATH，并沿用当前 Codex 登录。"""

        self._client = AppServerClient(expected_version="0.144.0")
        await self._client.start()
        return self._client

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.close()


def _client(workdir: Path) -> _AsyncCtx:
    return _AsyncCtx(workdir)
