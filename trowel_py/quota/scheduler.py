"""从 LLM 配置加载 GLM 账号，并用单个后台任务错峰轮询额度。"""

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
# 同一轮内在前一个账号读取结束后留出间隔，避免连续访问 GLM 额度接口。
DEFAULT_STAGGER_S = 1.0


class QuotaFetcher(Protocol):
    """定义调度器读取单个账号额度所需的异步接口。"""

    async def fetch(
        self, account_id: str, api_key: str
    ) -> QuotaSnapshot:  # pragma: no cover - 仅用于类型协议
        """读取指定账号的额度快照。

        Args:
            account_id: Trowel 用来区分额度账号的本地 ID。
            api_key: 该账号调用 GLM 额度接口所用的 API key。

        Returns:
            该账号本次读取的统一额度快照。
        """

        ...


@dataclass(frozen=True)
class GlmAccount:
    """保存轮询一个 GLM 账号所需的本地标识、凭据和接口地址。

    Attributes:
        account_id: Trowel 用来区分额度账号的本地 ID。
        api_key: 调用 GLM 额度接口所用的 API key。
        host: 请求 GLM 额度时使用的 HTTPS 根地址，不含客户端追加的 API 路径。
    """

    account_id: str
    api_key: str
    host: str = "https://open.bigmodel.cn"


class QuotaScheduler:
    """顺序轮询 GLM 账号，并把最新额度写入读模型。"""

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
        """配置待轮询账号、额度客户端和轮询时间。

        Args:
            accounts: 每轮按给定顺序读取的 GLM 账号。
            client: 读取单个账号额度的异步客户端。
            read_model: 保存各账号最新额度快照的进程内读模型。
            interval_s: 一轮账号读取结束后等待的秒数。
            stagger_s: 前一个账号读取结束后、读取下一个账号前等待的秒数。
            sleep_fn: 执行异步等待的函数；为 None 时使用 ``asyncio.sleep``。
        """

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
        """返回当前调度器持有的后台轮询任务。"""

        return tuple(self._tasks)

    async def start(self) -> None:
        """若尚未启动，则创建一个额度轮询后台任务；重复调用不会增加任务。"""

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
        """取消并等待现有轮询任务结束，再重置为未启动状态。"""

        self._stopping = True
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    async def _loop(self) -> None:
        """按顺序读取账号额度，在账号间错峰，并在整轮结束后等待固定间隔。"""

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
        """读取一个账号的额度，并把客户端返回的快照写入读模型。

        客户端返回的错误状态也会写入；客户端抛出的异常只记录日志，不会中断后续
        账号的轮询。

        Args:
            account: 本次要读取的 GLM 账号。
        """

        try:
            snapshot = await self._client.fetch(account.account_id, account.api_key)
        except Exception:
            logger.warning(
                "[quota] glm poll raised for %s", account.account_id, exc_info=True
            )
            return
        self._read_model.update(snapshot)


def load_glm_accounts(config: object | None = None) -> list[GlmAccount]:
    """从当前 LLM 配置中读取符合 GLM 轮询条件的账号。

    ``base_url`` 必须使用 HTTPS，且解析后的主机名必须精确等于
    ``open.bigmodel.cn``；API key 必须非空且不能以 ``<`` 开头。默认配置读取
    失败或任一条件不满足时返回空列表。

    Args:
        config: 提供 ``base_url`` 和 ``api_key`` 的 LLM 配置；为 None 时读取应用
            配置。

    Returns:
        可交给额度调度器轮询的 GLM 账号；配置不可用时为空列表。
    """

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
    # 主机名不能用子串判断，否则可能把 API key 发给仿冒域名。
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
