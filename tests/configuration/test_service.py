"""验证连接、catalog、会话配置和任务绑定的不变量。"""

from __future__ import annotations

import pytest

from tests.configuration.support import FakeCatalogFetcher, build_service
from trowel_py.configuration.catalog import FetchedCatalog, FetchedModel
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.models import (
    ConnectionDraft,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
    SessionConfigurationDraft,
    TaskId,
)


def _deepseek_codex() -> ConnectionDraft:
    """返回已通过真实运行验证的 Codex Responses 连接草稿。"""

    return ConnectionDraft(
        name="DeepSeek A",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        base_url="https://api.deepseek.com/v1",
    )


def test_connection_read_model_never_contains_secret() -> None:
    service, repository = build_service()
    created = service.create_connection(_deepseek_codex())

    updated = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="canary-secret-value",
    )

    assert updated.auth.status == "configured"
    assert "canary-secret-value" not in repr(updated)
    assert "canary-secret-value" not in str(updated.to_wire())
    assert repository.read_secret(created.id, SecretKind.API_KEY) == (
        "canary-secret-value"
    )


@pytest.mark.asyncio
async def test_secret_rotation_invalidates_the_old_model_catalog() -> None:
    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    created = service.create_connection(_deepseek_codex())
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="first-key",
    )
    fetched = await service.fetch_models(
        created.id,
        expected_version=with_secret.version,
    )
    assert fetched.status == "ready"

    rotated = service.write_secret(
        created.id,
        expected_version=fetched.connection_version,
        kind=SecretKind.API_KEY,
        value="second-key",
    )

    assert rotated.catalog.status == "stale"
    assert rotated.catalog.models == ()
    assert rotated.identity_version > with_secret.identity_version


@pytest.mark.asyncio
async def test_name_only_update_preserves_current_catalog_without_hidden_token() -> None:
    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    created = service.create_connection(_deepseek_codex())
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )
    fetched = await service.fetch_models(
        created.id, expected_version=with_secret.version
    )

    renamed = service.update_connection(
        created.id,
        expected_version=fetched.connection_version,
        draft=ConnectionDraft(
            name="DeepSeek renamed",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://api.deepseek.com/v1",
        ),
    )

    assert renamed.name == "DeepSeek renamed"
    assert renamed.catalog.status == "ready"
    assert renamed.catalog.models == ("deepseek-v4-flash",)


@pytest.mark.asyncio
async def test_same_provider_accounts_have_distinct_catalog_identities() -> None:
    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    first = service.create_connection(_deepseek_codex())
    second = service.create_connection(
        ConnectionDraft(
            name="DeepSeek B",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_CUSTOM,
            protocol=ProtocolKind.OPENAI_RESPONSES,
            base_url="https://api.deepseek.com/v1",
        )
    )
    first_secret = service.write_secret(
        first.id,
        expected_version=first.version,
        kind=SecretKind.API_KEY,
        value="first-key",
    )
    second_secret = service.write_secret(
        second.id,
        expected_version=second.version,
        kind=SecretKind.API_KEY,
        value="second-key",
    )

    first_catalog = await service.fetch_models(
        first.id, expected_version=first_secret.version
    )
    second_catalog = await service.fetch_models(
        second.id, expected_version=second_secret.version
    )

    assert first_catalog.request_identity != second_catalog.request_identity


def test_delete_and_readd_secret_keeps_monotonic_identity_version() -> None:
    service, _repository = build_service()
    created = service.create_connection(_deepseek_codex())
    first = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="first-key",
    )
    deleted = service.write_secret(
        created.id,
        expected_version=first.version,
        kind=SecretKind.API_KEY,
        value=None,
    )
    readded = service.write_secret(
        created.id,
        expected_version=deleted.version,
        kind=SecretKind.API_KEY,
        value="second-key",
    )

    assert deleted.auth.status == "missing"
    assert deleted.secret_versions["api_key"] == 2
    assert readded.auth.status == "configured"
    assert readded.secret_versions["api_key"] == 3


@pytest.mark.asyncio
async def test_late_model_request_cannot_overwrite_a_newer_identity() -> None:
    service, _repository = build_service()
    created = service.create_connection(_deepseek_codex())
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="first-key",
    )

    def rotate_while_request_is_running() -> None:
        service.write_secret(
            created.id,
            expected_version=with_secret.version,
            kind=SecretKind.API_KEY,
            value="second-key",
        )

    service.catalog_fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        ),
        before_return=rotate_while_request_is_running,
    )

    with pytest.raises(ConfigurationError) as raised:
        await service.fetch_models(
            created.id,
            expected_version=with_secret.version,
        )

    assert raised.value.code == "STALE_REQUEST"
    current = service.get_connection(created.id)
    assert current.secret_versions["api_key"] == 2
    assert current.catalog.status in {"idle", "stale"}


@pytest.mark.asyncio
async def test_verified_catalog_can_create_session_configuration_and_binding() -> None:
    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    created = service.create_connection(_deepseek_codex())
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
    current = service.get_connection(created.id)

    session = service.create_session_configuration(
        SessionConfigurationDraft(
            name="Codex DeepSeek high",
            connection_id=created.id,
            model="deepseek-v4-flash",
            effort="high",
        ),
        expected_connection_version=fetched.connection_version,
    )
    binding = service.put_task_binding(
        TaskId.MEMORY_WEEKLY,
        session_configuration_id=session.id,
        expected_version=0,
    )

    assert session.connection_identity_version == current.identity_version
    assert session.capability.status == "verified"
    assert binding.task_id == TaskId.MEMORY_WEEKLY
    assert binding.session_configuration_id == session.id

    renamed = service.update_session_configuration(
        session.id,
        expected_version=session.version,
        expected_connection_version=current.version,
        draft=SessionConfigurationDraft(
            name="Codex DeepSeek xhigh",
            connection_id=created.id,
            model="deepseek-v4-flash",
            effort="xhigh",
        ),
    )
    cleared_binding = service.delete_task_binding(
        TaskId.MEMORY_WEEKLY, expected_version=binding.version
    )
    rebound = service.put_task_binding(
        TaskId.MEMORY_WEEKLY,
        session_configuration_id=renamed.id,
        expected_version=cleared_binding.version,
    )
    cleared_again = service.delete_task_binding(
        TaskId.MEMORY_WEEKLY, expected_version=rebound.version
    )
    service.delete_session_configuration(renamed.id, expected_version=renamed.version)

    assert renamed.name == "Codex DeepSeek xhigh"
    assert cleared_binding.version == binding.version + 1
    assert cleared_binding.session_configuration_id is None
    assert rebound.version == cleared_binding.version + 1
    assert cleared_again.version == rebound.version + 1
    assert service.list_task_bindings() == (cleared_again,)
    assert service.list_session_configurations() == ()


@pytest.mark.asyncio
async def test_last_choice_only_changes_through_success_callback() -> None:
    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    created = service.create_connection(_deepseek_codex())
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )
    fetched = await service.fetch_models(
        created.id, expected_version=with_secret.version
    )
    session = service.create_session_configuration(
        SessionConfigurationDraft(
            name="准备创建",
            connection_id=created.id,
            model="deepseek-v4-flash",
            effort="high",
        ),
        expected_connection_version=fetched.connection_version,
    )

    assert service.get_connection(created.id).last_session_choice is None

    recorded = service.record_last_session_choice(
        created.id,
        expected_version=fetched.connection_version,
        model=session.model,
        effort=session.effort,
    )

    assert recorded.last_session_choice == {
        "model": "deepseek-v4-flash",
        "effort": "high",
    }


@pytest.mark.asyncio
async def test_session_configuration_becomes_stale_when_catalog_check_fails() -> None:
    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    created = service.create_connection(_deepseek_codex())
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )
    fetched = await service.fetch_models(
        created.id, expected_version=with_secret.version
    )
    session = service.create_session_configuration(
        SessionConfigurationDraft(
            name="Codex DeepSeek high",
            connection_id=created.id,
            model="deepseek-v4-flash",
            effort="high",
        ),
        expected_connection_version=fetched.connection_version,
    )

    class AuthFailureFetcher:
        """模拟上游拒绝当前凭据。"""

        async def fetch(self, **_kwargs: object) -> FetchedCatalog:
            """返回不含上游正文的稳定认证失败。"""

            raise ConfigurationError(
                "AUTH_FAILED", "模型服务拒绝了当前凭据", status_code=401
            )

    service.catalog_fetcher = AuthFailureFetcher()
    with pytest.raises(ConfigurationError):
        await service.fetch_models(
            created.id,
            expected_version=fetched.connection_version,
        )

    stale = service.get_session_configuration(session.id)
    assert stale.availability == "stale"
    assert stale.disabled_reason == "catalog_not_ready"


def test_agent_defaults_can_be_saved_and_reset() -> None:
    service, _repository = build_service()

    saved = service.put_agent_defaults(
        expected_version=0,
        session_configuration_id=None,
        permission="workspace-write",
        memory_enabled=False,
        profile_enabled=True,
        self_enabled=False,
    )
    reset = service.delete_agent_defaults(expected_version=saved.version)

    assert saved.version == 1
    assert reset.version == saved.version + 1
    assert reset.memory_enabled is True
    assert reset.profile_enabled is True
    assert reset.self_enabled is True

    saved_again = service.put_agent_defaults(
        expected_version=reset.version,
        session_configuration_id=None,
        permission="read-only",
        memory_enabled=True,
        profile_enabled=False,
        self_enabled=True,
    )
    assert saved_again.version == reset.version + 1


def test_stale_agent_defaults_update_cannot_overwrite_newer_value() -> None:
    service, _repository = build_service()
    saved = service.put_agent_defaults(
        expected_version=0,
        session_configuration_id=None,
        permission="read-only",
        memory_enabled=True,
        profile_enabled=True,
        self_enabled=True,
    )

    with pytest.raises(ConfigurationError) as raised:
        service.put_agent_defaults(
            expected_version=0,
            session_configuration_id=None,
            permission="workspace-write",
            memory_enabled=False,
            profile_enabled=False,
            self_enabled=False,
        )

    assert raised.value.code == "VERSION_CONFLICT"
    assert service.get_agent_defaults() == saved


def test_unknown_model_cannot_be_saved_as_available() -> None:
    service, _repository = build_service()
    created = service.create_connection(_deepseek_codex())

    with pytest.raises(ConfigurationError) as raised:
        service.create_session_configuration(
            SessionConfigurationDraft(
                name="未经验证",
                connection_id=created.id,
                model="future-model",
                effort="high",
            ),
            expected_connection_version=created.version,
        )

    assert raised.value.code in {"CATALOG_STALE", "CAPABILITY_UNKNOWN"}


def test_codex_official_rejects_irrelevant_models_url() -> None:
    service, _repository = build_service()

    with pytest.raises(ConfigurationError) as raised:
        service.create_connection(
            ConnectionDraft(
                name="Codex official",
                runtime=RuntimeKind.CODEX,
                kind=ConnectionKind.CODEX_OFFICIAL,
                protocol=ProtocolKind.CODEX_OFFICIAL,
                login_directory="~/.codex",
                models_url="https://api.openai.com/v1/models",
            )
        )

    assert raised.value.code == "CONNECTION_SHAPE_INVALID"


def test_soft_delete_keeps_history_but_removes_secret_and_new_catalog_entry() -> None:
    service, repository = build_service()
    created = service.create_connection(_deepseek_codex())
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key-to-delete",
    )

    service.delete_connection(created.id, expected_version=with_secret.version)

    assert service.list_connections() == ()
    assert repository.get_connection(created.id, include_deleted=True) is not None
    assert repository.read_secret(created.id, SecretKind.API_KEY) is None
