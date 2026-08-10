"""验证配置仓储在真实 SQLite 文件上的事务并发语义。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Timer

import pytest

from tests.configuration.support import (
    IsolatedClaudeConnectionHomeStore,
    IsolatedCodexConnectionHomeStore,
)
from trowel_py.configuration.catalog import FetchedCatalog, FetchedModel
from trowel_py.configuration.models import (
    ConnectionDraft,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
)
from trowel_py.configuration.repository import ConfigurationRepository
from trowel_py.configuration.service import ConfigurationService
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations


class _ImmediateCatalogFetcher:
    """在一次事件循环切换后返回固定的真实模型目录形状。"""

    async def fetch(self, **_kwargs: object) -> FetchedCatalog:
        """让调用方进入异步恢复路径，再返回单模型目录。"""

        await asyncio.sleep(0)
        return FetchedCatalog(
            models=(FetchedModel(id="glm-5.2"),),
            source_endpoint="https://runtime.example/v1/models",
        )


def test_atomic_write_waits_for_existing_writer_before_reading(tmp_path: Path) -> None:
    """读后写操作应等待已有写者提交，不能在事务升级时立即报锁冲突。"""

    database_path = tmp_path / "configuration.db"
    setup_connection = create_db(database_path)
    run_migrations(setup_connection)
    setup_service = ConfigurationService(
        ConfigurationRepository(setup_connection),
        claude_homes=IsolatedClaudeConnectionHomeStore(),
        codex_homes=IsolatedCodexConnectionHomeStore(),
    )
    created = setup_service.create_connection(
        ConnectionDraft(
            name="Claude E2E",
            runtime=RuntimeKind.CLAUDE_CODE,
            kind=ConnectionKind.CLAUDE_COMPATIBLE,
            protocol=ProtocolKind.ANTHROPIC_MESSAGES,
            base_url="https://runtime.example/v1",
        )
    )
    setup_connection.commit()
    setup_connection.close()

    blocker = create_db(database_path)
    contender = create_db(database_path)
    blocker.execute("BEGIN IMMEDIATE")
    release_blocker = Timer(0.1, blocker.commit)
    release_blocker.start()
    try:
        service = ConfigurationService(
            ConfigurationRepository(contender),
            claude_homes=IsolatedClaudeConnectionHomeStore(),
            codex_homes=IsolatedCodexConnectionHomeStore(),
        )
        updated = service.write_secret(
            created.id,
            expected_version=created.version,
            kind=SecretKind.API_KEY,
            value="test-secret",
        )

        assert updated.version == created.version + 1
    finally:
        release_blocker.join()
        contender.rollback()
        blocker.rollback()
        contender.close()
        blocker.close()


@pytest.mark.asyncio
async def test_fetch_models_keeps_event_loop_responsive_while_waiting_for_writer(
    tmp_path: Path,
) -> None:
    """目录持久化等待写锁时仍应允许事件循环释放已有写者。"""

    database_path = tmp_path / "configuration.db"
    connection = create_db(database_path)
    run_migrations(connection)
    service = ConfigurationService(
        ConfigurationRepository(connection),
        catalog_fetcher=_ImmediateCatalogFetcher(),
        claude_homes=IsolatedClaudeConnectionHomeStore(),
        codex_homes=IsolatedCodexConnectionHomeStore(),
    )
    created = service.create_connection(
        ConnectionDraft(
            name="Claude E2E",
            runtime=RuntimeKind.CLAUDE_CODE,
            kind=ConnectionKind.CLAUDE_COMPATIBLE,
            protocol=ProtocolKind.ANTHROPIC_MESSAGES,
            base_url="https://runtime.example/v1",
        )
    )
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="test-secret",
    )
    connection.commit()
    connection.execute("PRAGMA busy_timeout=250")

    blocker = create_db(database_path)
    blocker.execute("BEGIN IMMEDIATE")
    release_handle = asyncio.get_running_loop().call_later(0.05, blocker.commit)
    try:
        catalog = await service.fetch_models(
            created.id,
            expected_version=with_secret.version,
        )

        assert catalog.models == ("glm-5.2",)
    finally:
        release_handle.cancel()
        connection.rollback()
        blocker.rollback()
        connection.close()
        blocker.close()
