"""以单个顺序任务错峰轮询 GLM 账户，并隔离单账户失败。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import QuotaSnapshot

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 300.0
# 同一轮内错开账户请求，避免同时命中 provider。
DEFAULT_STAGGER_S = 1.0


class QuotaFetcher(Protocol):
    async def fetch(
        self, account_id: str, api_key: str
    ) -> QuotaSnapshot:  # pragma: no cover - 仅用于类型协议
        ...


@dataclass(frozen=True)
class GlmAccount:
    account_id: str
    api_key: str
    host: str = "https://open.bigmodel.cn"


class QuotaScheduler:
    def __init__(
        self,
        accounts: Sequence[GlmAccount],
        client: QuotaFetcher,
        read_model: QuotaReadModel,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        stagger_s: float = DEFAULT_STAGGER_S,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._accounts: tuple[GlmAccount, ...] = tuple(accounts)
        self._client = client
        self._read_model = read_model
        self._interval_s = interval_s
        self._stagger_s = stagger_s
        self._sleep = sleep_fn or asyncio.sleep
        self._tasks: list[asyncio.Task[None]] = []
        self._started = False
        self._stopping = False

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._tasks)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stopping = False
        logger.info(
            "[quota] scheduler started (%d account(s), every %ss)",
            len(self._accounts),
            self._interval_s,
        )
        self._tasks.append(asyncio.create_task(self._loop(), name="quota-poll"))

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    async def _loop(self) -> None:
        while not self._stopping:
            for index, account in enumerate(self._accounts):
                if self._stopping:
                    break
                if index > 0:
                    await self._sleep(self._stagger_s)
                await self._poll_once(account)
            if self._stopping:
                break
            await self._sleep(self._interval_s)

    async def _poll_once(self, account: GlmAccount) -> None:
        try:
            snapshot = await self._client.fetch(account.account_id, account.api_key)
        except Exception:
            # 已知失败由 client 转为状态；此处兜住未知异常，避免终止整条轮询链。
            logger.warning(
                "[quota] glm poll raised for %s", account.account_id, exc_info=True
            )
            return
        self._read_model.update(snapshot)


def load_glm_accounts(config: object | None = None) -> list[GlmAccount]:
    """只为合法的 active GLM 配置创建可轮询账户。"""
    if config is None:
        from trowel_py.config import load_llm_config

        try:
            config = load_llm_config()
        except Exception:
            logger.warning("[quota] could not load llm config", exc_info=True)
            return []
    base_url = getattr(config, "base_url", "") or ""
    api_key = getattr(config, "api_key", "") or ""
    parts = urlsplit(base_url)
    # 必须精确匹配 HTTPS host，子串匹配会把 API key 泄露给仿冒域名。
    allowed_hosts = {"open.bigmodel.cn"}
    if (
        parts.scheme != "https"
        or parts.hostname not in allowed_hosts
        or not api_key
        or api_key.startswith("<")
    ):
        return []
    host = f"{parts.scheme}://{parts.netloc}"
    return [GlmAccount(account_id="glm", api_key=api_key, host=host)]
