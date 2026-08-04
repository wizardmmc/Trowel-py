"""验证配置 HTTP 契约、稳定错误码和 secret 不回显。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.configuration.support import build_service
from trowel_py.configuration import routes
from trowel_py.configuration.catalog import FetchedCatalog
from trowel_py.configuration.errors import ConfigurationError, version_conflict
from trowel_py.configuration.models import (
    ConnectionDraft,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
)
from trowel_py.configuration.repository import ConfigurationRepository
from trowel_py.configuration.routes import get_configuration_service, router
from trowel_py.configuration.service import ConfigurationService
from trowel_py.db.connection import create_db


def _client(service: ConfigurationService | None = None) -> TestClient:
    """创建只装配配置领域并注入内存主库的测试应用。"""

    if service is None:
        service, _repository = build_service()
    app = FastAPI()
    app.include_router(router, prefix="/api/configuration")
    app.dependency_overrides[get_configuration_service] = lambda: service
    return TestClient(app)


def test_catalog_failure_commits_only_safe_diagnostic_state() -> None:
    service, _repository = build_service()
    created = service.create_connection(
        ConnectionDraft(
            name="DeepSeek A",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://api.deepseek.com/v1",
        )
    )
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="catalog-route-secret",
    )

    class AuthFailureFetcher:
        """模拟上游拒绝凭据。"""

        async def fetch(self, **_kwargs: object) -> FetchedCatalog:
            """抛出可安全持久化诊断 code 的认证失败。"""

            raise ConfigurationError(
                "AUTH_FAILED", "模型服务拒绝了当前凭据", status_code=401
            )

    service.catalog_fetcher = AuthFailureFetcher()

    with _client(service) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": with_secret.version},
        )

    current = service.get_connection(created.id)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_FAILED"
    assert "catalog-route-secret" not in response.text
    assert current.catalog.status == "error"
    assert current.catalog.error_code == "AUTH_FAILED"


def test_connection_and_secret_routes_never_echo_secret() -> None:
    with _client() as client:
        created_response = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
            },
        )
        assert created_response.status_code == 201
        created = created_response.json()["data"]
        canary = "route-canary-secret"

        secret_response = client.put(
            f"/api/configuration/connections/{created['id']}/secrets/api_key",
            json={
                "expected_version": created["version"],
                "action": "set",
                "value": canary,
            },
        )
        listed_response = client.get("/api/configuration/connections")

        assert secret_response.status_code == 200
        assert canary not in secret_response.text
        assert canary not in listed_response.text
        assert listed_response.json()["data"][0]["auth"]["status"] == "configured"


def test_secret_request_rejects_unknown_fields_without_echoing_input() -> None:
    with _client() as client:
        created = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
            },
        ).json()["data"]
        canary = "invalid-route-secret"

        response = client.put(
            f"/api/configuration/connections/{created['id']}/secrets/api_key",
            json={
                "expected_version": created["version"],
                "action": "set",
                "value": canary,
                "unexpected": canary,
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_SECRET_COMMAND"
        assert canary not in response.text


def test_secret_request_rejects_malformed_json_without_echoing_input() -> None:
    with _client() as client:
        created = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
            },
        ).json()["data"]
        canary = "malformed-route-secret"

        response = client.put(
            f"/api/configuration/connections/{created['id']}/secrets/api_key",
            content=f'{{"value":"{canary}"',
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_SECRET_COMMAND"
        assert canary not in response.text


def test_secret_request_rejects_oversized_value_without_echoing_input() -> None:
    with _client() as client:
        created = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
            },
        ).json()["data"]
        canary = "oversized-route-secret" * 1_000

        response = client.put(
            f"/api/configuration/connections/{created['id']}/secrets/api_key",
            json={
                "expected_version": created["version"],
                "action": "set",
                "value": canary,
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_SECRET_COMMAND"
        assert canary not in response.text


def test_version_conflict_uses_stable_error_code() -> None:
    with _client() as client:
        created = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
            },
        ).json()["data"]

        response = client.delete(
            f"/api/configuration/connections/{created['id']}",
            params={"expected_version": 99},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "VERSION_CONFLICT"


def test_framework_validation_error_does_not_echo_mistaken_secret_field() -> None:
    with _client() as client:
        canary = "mistaken-connection-secret"

        response = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": canary,
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REQUEST"
        assert canary not in response.text


def test_domain_error_rolls_back_partial_secret_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """领域错误先穿过请求事务回滚，再由 route 转成脱敏响应。"""

    database_path = tmp_path / "configuration.db"
    missing_config = tmp_path / "missing.toml"

    class RejectingUpdateRepository(ConfigurationRepository):
        """模拟 secret 写入后连接乐观更新发生竞争。"""

        def update_connection(self, *args: object, **kwargs: object) -> None:
            """拒绝连接更新，逼出多步骤写入的回滚路径。"""

            raise version_conflict()

    monkeypatch.setattr(routes, "create_db", lambda: create_db(database_path))
    monkeypatch.setattr(routes, "ConfigurationRepository", RejectingUpdateRepository)
    monkeypatch.setattr(routes, "find_config_path", lambda: missing_config)
    app = FastAPI()
    app.include_router(router, prefix="/api/configuration")

    with TestClient(app) as client:
        created = client.post(
            "/api/configuration/connections",
            json={
                "name": "DeepSeek A",
                "runtime": "codex",
                "kind": "codex_custom",
                "protocol": "openai_responses",
                "base_url": "https://api.deepseek.com/v1",
            },
        ).json()["data"]
        response = client.put(
            f"/api/configuration/connections/{created['id']}/secrets/api_key",
            json={
                "expected_version": created["version"],
                "action": "set",
                "value": "rollback-canary-secret",
            },
        )

    connection = create_db(database_path)
    secret_count = connection.execute(
        "SELECT COUNT(*) FROM configuration_secrets"
    ).fetchone()[0]
    stored_version = connection.execute(
        "SELECT version FROM configuration_connections WHERE id = ?",
        (created["id"],),
    ).fetchone()[0]
    connection.close()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VERSION_CONFLICT"
    assert "rollback-canary-secret" not in response.text
    assert secret_count == 0
    assert stored_version == created["version"]
