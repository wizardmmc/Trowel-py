"""验证配置 HTTP 契约、稳定错误码和 secret 不回显。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.configuration.support import build_service
from trowel_py.configuration import routes
from trowel_py.codex_host.catalog import parse_model_list_page
from trowel_py.configuration.catalog import FetchedCatalog, FetchedModel
from trowel_py.configuration.claude_home import ClaudeConnectionHomeStore
from trowel_py.configuration.codex_home import CodexConnectionHomeStore
from trowel_py.configuration.errors import ConfigurationError, version_conflict
from trowel_py.configuration.models import (
    CodexCatalogEntry,
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


def _client(
    service: ConfigurationService | None = None,
    *,
    agent_hub: Any | None = None,
) -> TestClient:
    """创建只装配配置领域并注入内存主库的测试应用。"""

    if service is None:
        service, _repository = build_service()
    app = FastAPI()
    if agent_hub is not None:
        app.state.agent_hub = agent_hub
    app.include_router(router, prefix="/api/configuration")
    app.dependency_overrides[get_configuration_service] = lambda: service
    return TestClient(app)


def test_configuration_transactions_finish_before_response_is_sent() -> None:
    """全部配置端点都应在客户端收到成功响应前提交并关闭事务。"""

    transaction_dependencies = [
        dependency
        for route in router.routes
        if hasattr(route, "dependant")
        for dependency in route.dependant.dependencies
        if dependency.call is get_configuration_service
    ]

    assert transaction_dependencies
    assert all(
        dependency.scope == "function" for dependency in transaction_dependencies
    )


class _NativeCodexCatalogHub:
    """提供从真实 Codex 录制解析出的原生模型目录。"""

    def __init__(self) -> None:
        """读取仓库内 Codex 0.144.0 的真实 ``model/list`` 录制。"""

        fixture = (
            Path(__file__).parents[1] / "codex_host/fixtures/model-list-0.144.0.json"
        )
        self.models, _cursor = parse_model_list_page(
            json.loads(fixture.read_text(encoding="utf-8"))
        )

    async def list_codex_models(self) -> list[dict[str, Any]]:
        """按 Codex 原生顺序返回标准化模型记录。"""

        return self.models


class _CodexUpstreamCatalogFetcher:
    """返回包含交互模型和非交互模型的上游目录。"""

    async def fetch(self, **_kwargs: object) -> FetchedCatalog:
        """模拟 Lab 模型端点的真实混合列表。"""

        return FetchedCatalog(
            models=tuple(
                FetchedModel(id=model_id)
                for model_id in (
                    "gpt-image-1",
                    "gpt-5.6-terra",
                    "gpt-5.6-sol",
                )
            ),
            source_endpoint="https://lab.example/v1/models",
        )


class _DeepSeekCodexCatalogFetcher:
    """返回 Codex 原生目录不认识的 DeepSeek 交互模型。"""

    async def fetch(self, **_kwargs: object) -> FetchedCatalog:
        """模拟新供应商首次获取 pro/flash 候选。"""

        return FetchedCatalog(
            models=(
                FetchedModel(id="deepseek-v4-pro"),
                FetchedModel(id="deepseek-v4-flash"),
            ),
            source_endpoint="https://api.deepseek.com/v1/models",
        )


class _CountingCodexCatalogHub:
    """记录 Agent 选项读取是否错误启动了 Codex manager。"""

    def __init__(self) -> None:
        """初始化原生目录读取次数。"""

        self.calls = 0

    async def list_codex_models_for_launch(self, _launch: Any) -> list[dict[str, Any]]:
        """记录一次不应发生的读取并返回空目录。"""

        self.calls += 1
        return []


class _EmptyCodexCatalogHub:
    """模拟 Codex 原生目录请求成功但没有返回任何模型。"""

    async def list_codex_models_for_launch(self, _launch: Any) -> list[dict[str, Any]]:
        """返回不能用于判断 GPT 交互模型范围的空目录。"""

        return []


class _OfficialAccountHub:
    """模拟 Codex 0.144.0 app-server 的账号与模型接口。"""

    def __init__(self) -> None:
        """记录账号读取和登录启动调用。"""

        self.read_calls = 0
        self.login_calls = 0
        self.catalog_launches: list[Any] = []

    async def read_codex_account_for_launch(self, _launch: Any) -> dict[str, Any]:
        """返回与上游 `account/read` 对齐的脱敏账号摘要。"""

        self.read_calls += 1
        return {
            "status": "logged_in",
            "email": "user@example.com",
            "plan_type": "pro",
            "auth_mode": "chatgpt",
        }

    async def start_codex_account_login_for_launch(
        self, _launch: Any
    ) -> dict[str, Any]:
        """返回与上游 device-code 登录响应对齐的字段。"""

        self.login_calls += 1
        return {
            "login_id": "login-1",
            "verification_url": "https://auth.openai.com/codex/device",
            "user_code": "ABCD-1234",
        }

    async def list_codex_models_for_launch(self, launch: Any) -> list[dict[str, Any]]:
        """返回真实录制解析后的 Official 原生模型目录。"""

        self.catalog_launches.append(launch)
        return _NativeCodexCatalogHub().models


class _CodexMaintenanceHub:
    """模拟按 connection ID 独占全部 Codex manager identity 的维护门禁。"""

    def __init__(self, *, available: bool) -> None:
        """保存是否允许进入维护态及调用账本。"""

        self.available = available
        self.started: list[str] = []
        self.ended: list[str] = []

    async def begin_codex_connection_maintenance(self, connection_id: str) -> bool:
        """记录维护请求，并模拟活动会话拒绝。"""

        self.started.append(connection_id)
        return self.available

    def end_codex_connection_maintenance(self, connection_id: str) -> None:
        """记录成功进入维护态后的解除动作。"""

        self.ended.append(connection_id)


@pytest.mark.asyncio
async def test_codex_model_fetch_returns_native_order_and_efforts() -> None:
    """设置页下载模型时附带 Codex 原生顺序和完整 effort 元数据。"""

    service, _repository = build_service(fetcher=_CodexUpstreamCatalogFetcher())
    created = service.create_connection(
        ConnectionDraft(
            name="Codex Lab",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://lab.example/v1",
        )
    )
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )

    with _client(service, agent_hub=_NativeCodexCatalogHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": with_secret.version},
        )

    assert response.status_code == 200
    codex_catalog = response.json()["data"]["codex_catalog"]
    assert [item["id"] for item in codex_catalog] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]
    assert codex_catalog[0]["default_effort"] == "low"
    assert codex_catalog[0]["supported_efforts"] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert response.json()["data"]["models"] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]
    assert service.get_connection(created.id).catalog.models == (
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    )


@pytest.mark.asyncio
async def test_new_deepseek_codex_fetch_keeps_pro_and_flash_candidates() -> None:
    """全新 DeepSeek 供应商不需要先保存模型也能选择 pro/flash。"""

    service, _repository = build_service(fetcher=_DeepSeekCodexCatalogFetcher())
    created = service.create_connection(
        ConnectionDraft(
            name="DeepSeek",
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
        value="key",
    )

    with _client(service, agent_hub=_NativeCodexCatalogHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": with_secret.version},
        )

    assert response.status_code == 200
    assert response.json()["data"]["models"] == [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]
    assert [
        item["id"] for item in response.json()["data"]["codex_catalog"]
    ] == ["deepseek-v4-pro", "deepseek-v4-flash"]


@pytest.mark.asyncio
async def test_codex_model_fetch_rejects_empty_native_catalog() -> None:
    """原生目录为空时不得把上游图片等非交互模型自动装入候选。"""

    service, repository = build_service(fetcher=_CodexUpstreamCatalogFetcher())
    created = service.create_connection(
        ConnectionDraft(
            name="Codex Lab",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://lab.example/v1",
        )
    )
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )
    repository.connection.commit()
    status_before_fetch = service.get_connection(created.id).catalog.status

    with _client(service, agent_hub=_EmptyCodexCatalogHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": with_secret.version},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "CODEX_CATALOG_UNAVAILABLE"
    assert service.get_connection(created.id).catalog.status == status_before_fetch


@pytest.mark.asyncio
async def test_agent_options_do_not_share_codex_catalog_between_connections() -> None:
    """多个 Codex 供应商必须分别展示各自保存的有序模型选择。"""

    service, _repository = build_service(fetcher=_CodexUpstreamCatalogFetcher())
    ready_connections = []
    for name, base_url, selected_model in (
        ("Codex Alpha", "https://alpha.example/v1", "gpt-5.6-sol"),
        ("Codex Beta", "https://beta.example/v1", "gpt-5.6-terra"),
    ):
        draft = ConnectionDraft(
            name=name,
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url=base_url,
        )
        created = service.create_connection(draft)
        with_secret = service.write_secret(
            created.id,
            expected_version=created.version,
            kind=SecretKind.API_KEY,
            value=f"{name}-key",
        )
        fetched = await service.fetch_models(
            created.id, expected_version=with_secret.version
        )
        service.update_connection(
            created.id,
            expected_version=fetched.connection_version,
            draft=ConnectionDraft(
                **{
                    **draft.__dict__,
                    "codex_catalog": (
                        CodexCatalogEntry(
                            id=selected_model,
                            default_effort="high",
                            supported_efforts=("low", "high"),
                        ),
                    ),
                    "catalog_request_identity": fetched.request_identity,
                }
            ),
        )
        ready_connections.append(created.id)

    alpha_id, beta_id = ready_connections
    with _client(service) as client:
        response = client.get("/api/configuration/agent-options")

    assert response.status_code == 200
    options = {item["id"]: item for item in response.json()["data"]}
    assert [model["id"] for model in options[alpha_id]["models"]] == ["gpt-5.6-sol"]
    assert [model["id"] for model in options[beta_id]["models"]] == ["gpt-5.6-terra"]


@pytest.mark.asyncio
async def test_agent_options_use_saved_catalog_without_starting_codex_managers() -> None:
    """打开 Agent 页面只读持久设置，不能为每个 Codex 供应商启动 manager。"""

    service, _repository = build_service(fetcher=_CodexUpstreamCatalogFetcher())
    draft = ConnectionDraft(
        name="Codex Lab",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        base_url="https://lab.example/v1",
    )
    created = service.create_connection(draft)
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )
    fetched = await service.fetch_models(
        created.id,
        expected_version=with_secret.version,
    )
    service.update_connection(
        created.id,
        expected_version=fetched.connection_version,
        draft=ConnectionDraft(
            **{
                **draft.__dict__,
                "codex_catalog": (
                    CodexCatalogEntry(
                        id="gpt-5.6-terra",
                        default_effort="medium",
                        supported_efforts=("medium", "high"),
                    ),
                ),
                "catalog_request_identity": fetched.request_identity,
            }
        ),
    )
    hub = _CountingCodexCatalogHub()

    with _client(service, agent_hub=hub) as client:
        response = client.get("/api/configuration/agent-options")

    assert response.status_code == 200
    option = response.json()["data"][0]
    assert [model["id"] for model in option["models"]] == ["gpt-5.6-terra"]
    assert hub.calls == 0


def test_codex_official_account_status_and_login_use_native_app_server(
    tmp_path: Path,
) -> None:
    """Official 登录由 Codex app-server 完成，HTTP 只返回账号摘要和登录引导。"""

    service, _repository = build_service(official_account_root=tmp_path / "accounts")
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    hub = _OfficialAccountHub()

    with _client(service, agent_hub=hub) as client:
        status = client.get(
            f"/api/configuration/connections/{created.id}/official-account"
        )
        login = client.post(
            f"/api/configuration/connections/{created.id}/official-account/login"
        )

    assert status.status_code == 200
    assert status.json()["data"] == {
        "status": "logged_in",
        "email": "user@example.com",
        "plan_type": "pro",
        "auth_mode": "chatgpt",
        "login_id": None,
        "login_status": None,
        "login_error": None,
    }
    assert login.status_code == 200
    assert login.json()["data"]["user_code"] == "ABCD-1234"
    assert hub.read_calls == 1
    assert hub.login_calls == 1


def test_codex_official_fetches_native_models_without_api_key(tmp_path: Path) -> None:
    """Official 模型候选直接来自已登录 app-server，不要求 API key 或模型地址。"""

    service, _repository = build_service(official_account_root=tmp_path / "accounts")
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )

    with _client(service, agent_hub=_OfficialAccountHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": created.version},
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["models"][0] == "gpt-5.6-sol"
    assert payload["codex_catalog"][0]["id"] == "gpt-5.6-sol"
    assert payload["source_endpoint"] == "codex://model/list"


def test_codex_official_fetch_accepts_the_hidden_slot_editor_draft(
    tmp_path: Path,
) -> None:
    """设置页回传隐藏目录后的 Official 草稿仍应使用服务端托管槽获取模型。"""

    service, _repository = build_service(official_account_root=tmp_path / "accounts")
    created = service.create_connection(
        ConnectionDraft(
            name="Codex",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    editor_draft = {
        "name": "Codex",
        "runtime": "codex",
        "kind": "codex_official",
        "protocol": "codex_official",
        "base_url": None,
        "models_url": None,
        "login_directory": None,
        "proxy_url": None,
        "proxy_username": None,
        "claude_role_models": {},
        "codex_catalog": [],
        "catalog_request_identity": None,
    }

    with _client(service, agent_hub=_OfficialAccountHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": created.version, "draft": editor_draft},
        )

    assert response.status_code == 200
    assert response.json()["data"]["codex_catalog"][0]["id"] == "gpt-5.6-sol"


def test_codex_official_fetch_ignores_client_login_directory(tmp_path: Path) -> None:
    """Official 获取目录只能使用当前连接的服务端槽，不能采用客户端路径。"""

    service, repository = build_service(official_account_root=tmp_path / "accounts")
    created = service.create_connection(
        ConnectionDraft(
            name="Codex",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    row = repository.get_connection(created.id)
    assert row is not None
    hub = _OfficialAccountHub()

    with _client(service, agent_hub=hub) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={
                "expected_version": created.version,
                "draft": {
                    "name": "Codex",
                    "runtime": "codex",
                    "kind": "codex_official",
                    "protocol": "codex_official",
                    "login_directory": "/tmp/client-injected-codex-home",
                },
            },
        )

    assert response.status_code == 200
    assert len(hub.catalog_launches) == 1
    assert hub.catalog_launches[0].login_directory == row["login_directory"]


@pytest.mark.parametrize("field", ["base_url", "models_url"])
def test_codex_official_fetch_rejects_client_service_addresses(
    tmp_path: Path,
    field: str,
) -> None:
    """Official 草稿继续拒绝模型服务地址，不能退化成自定义供应商。"""

    service, _repository = build_service(official_account_root=tmp_path / "accounts")
    created = service.create_connection(
        ConnectionDraft(
            name="Codex",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    draft = {
        "name": "Codex",
        "runtime": "codex",
        "kind": "codex_official",
        "protocol": "codex_official",
        field: "https://client-injected.example/v1/models",
    }

    with _client(service, agent_hub=_OfficialAccountHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": created.version, "draft": draft},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CONNECTION_SHAPE_INVALID"


def test_codex_official_fetch_select_and_save_enables_agent_option(
    tmp_path: Path,
) -> None:
    """Official 从候选获取到有序选择保存后应立即成为可用 Agent 供应商。"""

    service, repository = build_service(official_account_root=tmp_path / "accounts")
    created = service.create_connection(
        ConnectionDraft(
            name="Codex",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    row = repository.get_connection(created.id)
    assert row is not None
    (Path(str(row["login_directory"])) / "auth.json").write_text(
        "oauth-canary", encoding="utf-8"
    )

    with _client(service, agent_hub=_OfficialAccountHub()) as client:
        fetched_response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": created.version},
        )
        assert fetched_response.status_code == 200
        fetched = fetched_response.json()["data"]
        selected_catalog = [fetched["codex_catalog"][1], fetched["codex_catalog"][0]]

        saved_response = client.put(
            f"/api/configuration/connections/{created.id}",
            json={
                "expected_version": fetched["connection_version"],
                "name": "Codex",
                "runtime": "codex",
                "kind": "codex_official",
                "protocol": "codex_official",
                "codex_catalog": selected_catalog,
                "catalog_request_identity": fetched["request_identity"],
            },
        )
        options_response = client.get("/api/configuration/agent-options")

    assert saved_response.status_code == 200
    assert options_response.status_code == 200
    option = options_response.json()["data"][0]
    assert option["available"] is True
    assert option["disabled_reason"] is None
    assert [model["id"] for model in option["models"]] == [
        selected_catalog[0]["id"],
        selected_catalog[1]["id"],
    ]


def test_catalog_failure_commits_only_safe_diagnostic_state() -> None:
    service, repository = build_service()
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
    repository.connection.commit()

    with _client(service, agent_hub=_NativeCodexCatalogHub()) as client:
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


def test_model_fetch_losing_to_secret_rotation_returns_stale_without_partial_catalog(
    tmp_path: Path,
) -> None:
    """远端请求期间另一连接换 key 后，旧请求必须返回 409 且不留目录。"""

    database_path = tmp_path / "configuration.db"
    first_connection = create_db(database_path)
    from trowel_py.db.migrate import run_migrations

    run_migrations(first_connection)
    first_repository = ConfigurationRepository(first_connection)

    class RotatingFetcher:
        """在返回上游模型前用独立数据库连接更换 API key。"""

        async def fetch(self, **_kwargs: object) -> FetchedCatalog:
            """提交竞争写入后返回本应被丢弃的旧请求结果。"""

            competing_connection = create_db(database_path)
            competing_service = ConfigurationService(
                ConfigurationRepository(competing_connection)
            )
            competing_service.write_secret(
                created.id,
                expected_version=with_secret.version,
                kind=SecretKind.API_KEY,
                value="new-key",
            )
            competing_connection.commit()
            competing_connection.close()
            return FetchedCatalog(
                models=(FetchedModel(id="deepseek-v4-flash"),),
                source_endpoint="https://api.deepseek.com/v1/models",
            )

    service = ConfigurationService(first_repository, catalog_fetcher=RotatingFetcher())
    created = service.create_connection(
        ConnectionDraft(
            name="DeepSeek",
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
        value="old-key",
    )
    first_connection.commit()

    with _client(service, agent_hub=_NativeCodexCatalogHub()) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/models:fetch",
            json={"expected_version": with_secret.version},
        )

    verification_connection = create_db(database_path)
    row = ConfigurationRepository(verification_connection).get_connection(created.id)
    catalog_count = verification_connection.execute(
        "SELECT COUNT(*) FROM configuration_model_catalogs WHERE connection_id = ?",
        (created.id,),
    ).fetchone()[0]
    verification_connection.close()
    first_connection.close()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STALE_REQUEST"
    assert row is not None
    assert row["version"] == with_secret.version + 1
    assert row["catalog_status"] != "ready"
    assert catalog_count == 0


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


def test_inherit_claude_config_route_returns_only_redacted_state(
    tmp_path: Path,
) -> None:
    """继承动作只返回是否已继承，不暴露连接家路径或内容。"""

    global_home = tmp_path / "global" / ".claude"
    global_home.mkdir(parents=True)
    (global_home / "CLAUDE.md").write_text("global rules", encoding="utf-8")
    homes = ClaudeConnectionHomeStore(
        tmp_path / "managed",
        global_home=global_home,
    )
    service, _repository = build_service(claude_homes=homes)
    created = service.create_connection(
        ConnectionDraft(
            name="Claude Provider",
            runtime=RuntimeKind.CLAUDE_CODE,
            kind=ConnectionKind.CLAUDE_COMPATIBLE,
            protocol=ProtocolKind.ANTHROPIC_MESSAGES,
            base_url="https://example.com/anthropic",
        )
    )

    with _client(service) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/claude-config/inherit",
            params={"expected_version": created.version},
        )

    assert response.status_code == 200
    assert response.json()["data"]["claude_config_inherited"] is True
    assert str(homes.root) not in response.text
    assert "global rules" not in response.text


def test_inherit_codex_config_route_copies_two_roots_without_exposing_paths(
    tmp_path: Path,
) -> None:
    """Codex 继承同时覆盖两处技能来源，但响应只返回状态。"""

    codex_source = tmp_path / "global" / ".codex"
    agents_source = tmp_path / "global" / ".agents"
    (codex_source / "skills" / "legacy").mkdir(parents=True)
    (agents_source / "skills" / "development-slice-workflow").mkdir(
        parents=True
    )
    (codex_source / "config.toml").write_text("model = 'gpt'", encoding="utf-8")
    (agents_source / "skills" / "development-slice-workflow" / "SKILL.md").write_text(
        "workflow body", encoding="utf-8"
    )
    homes = CodexConnectionHomeStore(
        tmp_path / "managed",
        global_codex_home=codex_source,
        global_agents_home=agents_source,
    )
    service, repository = build_service(codex_homes=homes)
    created = service.create_connection(
        ConnectionDraft(
            name="DeepSeek",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://api.deepseek.com/v1",
        )
    )

    with _client(service) as client:
        response = client.post(
            f"/api/configuration/connections/{created.id}/codex-config/inherit",
            params={"expected_version": created.version},
        )

    assert response.status_code == 200
    assert response.json()["data"]["codex_config_inherited"] is True
    assert str(homes.root) not in response.text
    assert "workflow body" not in response.text
    home = homes.ensure_home(created.id)
    repository.connection.commit()
    assert (home / "config.toml").is_file()
    assert (
        home / ".agents" / "skills" / "development-slice-workflow" / "SKILL.md"
    ).is_file()


def test_delete_custom_codex_connection_rejects_active_session(
    tmp_path: Path,
) -> None:
    """Custom 与 Official 一样，活动会话存在时不能删除其连接配置家。"""

    homes = CodexConnectionHomeStore(
        tmp_path / "managed",
        global_codex_home=tmp_path / "global-codex",
        global_agents_home=tmp_path / "global-agents",
    )
    service, repository = build_service(codex_homes=homes)
    created = service.create_connection(
        ConnectionDraft(
            name="DeepSeek",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://api.deepseek.com/v1",
        )
    )
    home = homes.ensure_home(created.id)
    repository.connection.commit()
    assert home.is_dir()
    hub = _CodexMaintenanceHub(available=False)

    with _client(service, agent_hub=hub) as client:
        response = client.delete(
            f"/api/configuration/connections/{created.id}",
            params={"expected_version": created.version},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONNECTION_IN_USE"
    assert home.is_dir()
    assert service.get_connection(created.id).id == created.id
    assert hub.started == [created.id]
    assert hub.ended == []


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
