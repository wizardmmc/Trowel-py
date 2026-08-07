"""验证连接、catalog、会话配置和任务绑定的不变量。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from tests.configuration.support import FakeCatalogFetcher, build_service
from trowel_py.codex_host.catalog import parse_model_list_page
from trowel_py.configuration.catalog import FetchedCatalog, FetchedModel
from trowel_py.configuration.claude_home import ClaudeConnectionHomeStore
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.models import (
    CodexCatalogEntry,
    ConnectionDraft,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
    SessionConfigurationDraft,
    TaskId,
)
from trowel_py.configuration.service import merge_codex_catalog


def test_merge_codex_catalog_keeps_custom_models_when_native_catalog_has_no_match() -> None:
    """DeepSeek 等自定义连接首次获取时仍应提供可选模型。"""

    fixture = Path(__file__).parents[1] / "codex_host/fixtures/model-list-0.144.0.json"
    native_models, _cursor = parse_model_list_page(
        json.loads(fixture.read_text(encoding="utf-8"))
    )

    merged = merge_codex_catalog(
        ("deepseek-v4-pro", "deepseek-v4-flash"),
        native_models=native_models,
    )

    assert [entry.id for entry in merged] == [
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    ]


def test_merge_codex_catalog_excludes_noninteractive_models_when_native_matches() -> None:
    """GPT 连接应以原生交互目录为准，不能把 image 模型加入候选。"""

    fixture = Path(__file__).parents[1] / "codex_host/fixtures/model-list-0.144.0.json"
    native_models, _cursor = parse_model_list_page(
        json.loads(fixture.read_text(encoding="utf-8"))
    )

    merged = merge_codex_catalog(
        ("gpt-image-1", "gpt-5.6-terra", "gpt-5.6-sol"),
        saved_entries=(
            CodexCatalogEntry(
                id="gpt-image-1",
                default_effort="high",
                supported_efforts=("high",),
            ),
        ),
        native_models=native_models,
    )

    assert [entry.id for entry in merged] == ["gpt-5.6-sol", "gpt-5.6-terra"]


@pytest.mark.asyncio
async def test_selecting_codex_models_does_not_change_runtime_identity() -> None:
    """Codex 已选模型属于会话选项，不能制造新的常驻 manager 身份。"""

    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    draft = ConnectionDraft(
        name="DeepSeek",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        base_url="https://api.deepseek.com/v1",
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

    selected = service.update_connection(
        created.id,
        expected_version=fetched.connection_version,
        draft=ConnectionDraft(
            **{
                **draft.__dict__,
                "codex_catalog": (
                    CodexCatalogEntry(
                        id="deepseek-v4-flash",
                        default_effort="high",
                        supported_efforts=("high",),
                    ),
                ),
                "catalog_request_identity": fetched.request_identity,
            }
        ),
    )

    assert selected.identity_version == with_secret.identity_version


@pytest.mark.asyncio
async def test_agent_options_exclude_saved_models_missing_from_compatible_catalog() -> None:
    """旧白名单可留在设置中辨认，但不兼容模型不能进入新会话。"""

    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="gpt-image-1"), FetchedModel(id="gpt-5.6-sol")),
            source_endpoint="https://lab.example/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    draft = ConnectionDraft(
        name="Lab",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        base_url="https://lab.example/v1",
        codex_catalog=(
            CodexCatalogEntry(id="gpt-image-1"),
            CodexCatalogEntry(id="gpt-5.6-sol", default_effort="high"),
        ),
    )
    created = service.create_connection(draft)
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="key",
    )
    catalog = await service.fetch_models(
        created.id, expected_version=with_secret.version
    )
    selected = service.update_connection(
        created.id,
        expected_version=catalog.connection_version,
        draft=ConnectionDraft(
            **{
                **draft.__dict__,
                "catalog_request_identity": catalog.request_identity,
            }
        ),
    )
    service.restrict_codex_catalog_candidates(
        created.id,
        catalog=catalog,
        candidates=(CodexCatalogEntry(id="gpt-5.6-sol", default_effort="high"),),
    )

    option = service.list_agent_connection_options()[0]

    assert selected.codex_catalog == draft.codex_catalog
    assert option["available"] is True
    assert option["models"][0]["id"] == "gpt-image-1"
    assert option["models"][0]["available"] is False
    assert option["models"][1]["available"] is True


def test_official_account_slot_migration_requires_separate_relogin(tmp_path: Path) -> None:
    """历史共享目录只改引用，不得把 OAuth 凭据复制进两个托管槽。"""

    root = tmp_path / "managed"
    service, repository = build_service(official_account_root=root)
    created = [
        service.create_connection(
            ConnectionDraft(
                name=name,
                runtime=RuntimeKind.CODEX,
                kind=ConnectionKind.CODEX_OFFICIAL,
                protocol=ProtocolKind.CODEX_OFFICIAL,
            )
        )
        for name in ("OpenAI A", "OpenAI B")
    ]
    shared = tmp_path / "legacy-codex"
    shared.mkdir()
    (shared / "auth.json").write_text("oauth-canary", encoding="utf-8")
    for item in created:
        repository.update_connection(
            item.id,
            expected_version=item.version,
            values={"version": item.version + 1, "login_directory": str(shared)},
        )

    assert service.migrate_official_account_slots() == 2

    for item in created:
        row = repository.get_connection(item.id)
        slot = root / item.id
        assert Path(row["login_directory"]) == slot
        assert slot.is_dir()
        assert not (slot / "auth.json").exists()
    assert (shared / "auth.json").read_text(encoding="utf-8") == "oauth-canary"


def test_official_launch_rejects_legacy_external_account_slot(tmp_path: Path) -> None:
    """启动迁移失败时不得继续使用共享或外部 CODEX_HOME。"""

    root = tmp_path / "managed"
    service, repository = build_service(official_account_root=root)
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    external = tmp_path / "legacy-codex"
    external.mkdir()
    repository.update_connection(
        created.id,
        expected_version=created.version,
        values={"version": created.version + 1, "login_directory": str(external)},
    )

    with pytest.raises(ConfigurationError) as caught:
        service.resolve_codex_catalog_launch(created.id)

    assert caught.value.code == "LOGIN_DIRECTORY_MISSING"


def test_official_account_slot_migration_ignores_unremovable_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """延迟清理再次失败时仍应允许设置和会话继续使用。"""

    root = tmp_path / "managed"
    tombstone = root / f".deleted-{uuid.uuid4()}-{uuid.uuid4().hex}"
    tombstone.mkdir(parents=True)
    service, _repository = build_service(official_account_root=root)

    def reject_cleanup(_path: Path) -> None:
        """模拟文件仍被其他进程占用。"""

        raise OSError("busy")

    monkeypatch.setattr("trowel_py.configuration.service.shutil.rmtree", reject_cleanup)

    assert service.migrate_official_account_slots() == 0
    assert tombstone.is_dir()
    assert "墓碑仍无法清理" in caplog.text


def test_delete_official_connection_removes_only_its_managed_account_slot(
    tmp_path: Path,
) -> None:
    """删除 Official 供应商时必须删除其托管 OAuth 槽。"""

    root = tmp_path / "managed"
    service, _repository = build_service(official_account_root=root)
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    slot = root / created.id
    (slot / "auth.json").write_text("oauth-canary", encoding="utf-8")

    deletion = service.delete_connection(created.id, expected_version=created.version)
    service.repository.connection.commit()
    service.finalize_connection_storage_deletion(deletion)

    assert not slot.exists()


def test_startup_restores_account_slot_when_soft_delete_was_rolled_back(
    tmp_path: Path,
) -> None:
    """重启时必须把未提交删除留下的 OAuth 墓碑恢复给活动供应商。"""

    root = tmp_path / "managed"
    service, repository = build_service(official_account_root=root)
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    slot = root / created.id
    (slot / "auth.json").write_text("oauth-canary", encoding="utf-8")
    repository.connection.commit()

    deletion = service.delete_connection(created.id, expected_version=created.version)
    assert deletion is not None
    assert deletion.codex_home is not None
    assert deletion.codex_home.tombstone.is_dir()
    repository.connection.rollback()

    assert service.migrate_official_account_slots() == 0
    assert (slot / "auth.json").read_text(encoding="utf-8") == "oauth-canary"
    assert not deletion.codex_home.tombstone.exists()


def test_startup_cleans_account_slot_after_committed_soft_delete(tmp_path: Path) -> None:
    """软删除已提交但清理前退出时，重启应删除对应墓碑。"""

    root = tmp_path / "managed"
    service, repository = build_service(official_account_root=root)
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    slot = root / created.id
    (slot / "auth.json").write_text("oauth-canary", encoding="utf-8")
    repository.connection.commit()

    deletion = service.delete_connection(created.id, expected_version=created.version)
    assert deletion is not None
    repository.connection.commit()

    assert service.migrate_official_account_slots() == 0
    assert deletion.codex_home is not None
    assert not deletion.codex_home.tombstone.exists()


def test_delete_official_connection_rejects_symlinked_account_slot(
    tmp_path: Path,
) -> None:
    """托管槽被替换成符号链接时不得删除链接目标。"""

    root = tmp_path / "managed"
    service, _repository = build_service(official_account_root=root)
    created = service.create_connection(
        ConnectionDraft(
            name="OpenAI Pro",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    slot = root / created.id
    slot.rmdir()
    victim = tmp_path / "victim"
    victim.mkdir()
    canary = victim / "keep.txt"
    canary.write_text("keep", encoding="utf-8")
    slot.symlink_to(victim, target_is_directory=True)

    with pytest.raises(ConfigurationError, match="符号链接"):
        service.delete_connection(created.id, expected_version=created.version)

    assert canary.read_text(encoding="utf-8") == "keep"
    assert slot.is_symlink()


@pytest.mark.asyncio
async def test_saved_agent_defaults_override_recent_session_without_restart() -> None:
    """设置页保存的配置、权限和三个注入开关应覆盖最近会话值。"""

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
    configuration = service.create_session_configuration(
        SessionConfigurationDraft(
            name="DeepSeek Flash",
            connection_id=created.id,
            model="deepseek-v4-flash",
            effort="high",
        ),
        expected_connection_version=fetched.connection_version,
    )
    service.put_agent_defaults(
        expected_version=0,
        session_configuration_id=configuration.id,
        permission="read-only",
        memory_enabled=False,
        profile_enabled=False,
        self_enabled=False,
    )

    resolved = service.resolve_agent_session_defaults(
        {
            "runtime": "claude_code",
            "model": "opus",
            "effort": "max",
            "permission_mode": "bypassPermissions",
            "memory_enabled": True,
            "profile_enabled": True,
        }
    )

    assert resolved == {
        "runtime": "codex",
        "connection_id": created.id,
        "model": "deepseek-v4-flash",
        "effort": "high",
        "permission_mode": "",
        "permission_preset": "read-only",
        "memory_enabled": False,
        "profile_enabled": False,
        "self_enabled": False,
    }


@pytest.mark.asyncio
async def test_runtime_launch_freezes_claude_connection_without_exposing_secret() -> (
    None
):
    """Agent 启动配置持有真实凭据，但脱敏身份和 repr 不得泄露。"""

    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="glm-5.2"),),
            source_endpoint="https://open.bigmodel.cn/api/anthropic/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    draft = ConnectionDraft(
        name="GLM A",
        runtime=RuntimeKind.CLAUDE_CODE,
        kind=ConnectionKind.CLAUDE_COMPATIBLE,
        protocol=ProtocolKind.ANTHROPIC_MESSAGES,
        base_url="https://open.bigmodel.cn/api/anthropic",
    )
    created = service.create_connection(draft)
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="runtime-canary-secret",
    )
    fetched = await service.fetch_models(
        created.id, expected_version=with_secret.version
    )
    ready = service.update_connection(
        created.id,
        expected_version=fetched.connection_version,
        draft=ConnectionDraft(
            **{
                **draft.__dict__,
                "claude_role_models": {"opus": "glm-5.2"},
                "catalog_request_identity": fetched.request_identity,
            }
        ),
    )

    launch = service.resolve_runtime_launch(ready.id, model="opus", effort=None)

    assert launch.connection_identity_version == ready.identity_version
    assert launch.claude_config_dir is not None
    assert Path(launch.claude_config_dir).is_dir()
    assert list(Path(launch.claude_config_dir).iterdir()) == []
    assert launch.claude_plugin_dir == str(service.claude_homes.shared_plugin_root)
    assert launch.api_key == "runtime-canary-secret"
    assert "runtime-canary-secret" not in repr(launch)
    settings = launch.claude_settings(proxy_base_url="http://127.0.0.1/private")
    assert settings["env"]["ANTHROPIC_BASE_URL"].endswith("/private")
    assert settings["model"] == "opus"
    assert service.list_agent_connection_options()[0]["models"] == [
        {
            "id": "opus",
            "display_name": "opus",
            "available": True,
            "disabled_reason": None,
            "efforts": [],
            "default_effort": None,
        }
    ]


def test_service_inherits_and_retains_claude_connection_home(tmp_path: Path) -> None:
    """设置域只接受用户主动继承，删除连接后保留原生历史根。"""

    global_home = tmp_path / "global" / ".claude"
    global_home.mkdir(parents=True)
    (global_home / "CLAUDE.md").write_text("global rules", encoding="utf-8")
    homes = ClaudeConnectionHomeStore(
        tmp_path / "managed",
        global_home=global_home,
    )
    service, repository = build_service(claude_homes=homes)
    created = service.create_connection(
        ConnectionDraft(
            name="Claude Provider",
            runtime=RuntimeKind.CLAUDE_CODE,
            kind=ConnectionKind.CLAUDE_COMPATIBLE,
            protocol=ProtocolKind.ANTHROPIC_MESSAGES,
            base_url="https://example.com/anthropic",
        )
    )

    assert created.claude_config_inherited is False
    inherited = service.inherit_global_claude_config(
        created.id,
        expected_version=created.version,
    )
    home = homes.home_for(created.id)
    (home / "projects").mkdir()

    assert inherited.claude_config_inherited is True
    assert (home / "CLAUDE.md").read_text(encoding="utf-8") == "global rules"

    deletion = service.delete_connection(created.id, expected_version=created.version)
    repository.connection.commit()
    service.finalize_connection_storage_deletion(deletion)

    assert deletion is not None
    assert deletion.claude_home is not None
    assert deletion.claude_home.tombstone.is_file()
    assert home / "projects" in homes.projects_roots()


def test_service_scans_all_saved_secrets_before_claude_inheritance(
    tmp_path: Path,
) -> None:
    """凭据 canary 来自设置域，不依赖全局 settings 的字段名推测。"""

    global_home = tmp_path / "global" / ".claude"
    global_home.mkdir(parents=True)
    secret = "saved-provider-secret"
    (global_home / "CLAUDE.md").write_text(secret, encoding="utf-8")
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
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value=secret,
    )

    with pytest.raises(ConfigurationError, match="已知凭据"):
        service.inherit_global_claude_config(
            created.id,
            expected_version=with_secret.version,
        )


@pytest.mark.asyncio
async def test_runtime_launch_builds_isolated_codex_provider_overrides(
    tmp_path: Path,
) -> None:
    """第三方 Codex 必须通过专属 env_key 启动，不能读取共享 OpenAI 登录。"""

    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="deepseek-v4-flash"),),
            source_endpoint="https://api.deepseek.com/v1/models",
        )
    )
    managed_root = tmp_path / "codex-accounts"
    service, _repository = build_service(
        fetcher=fetcher,
        official_account_root=managed_root,
    )
    draft = _deepseek_codex()
    created = service.create_connection(draft)
    with_secret = service.write_secret(
        created.id,
        expected_version=created.version,
        kind=SecretKind.API_KEY,
        value="deepseek-canary-secret",
    )
    fetched = await service.fetch_models(
        created.id, expected_version=with_secret.version
    )
    ready = service.update_connection(
        created.id,
        expected_version=fetched.connection_version,
        draft=ConnectionDraft(
            **{
                **draft.__dict__,
                "codex_catalog": (
                    CodexCatalogEntry(
                        id="deepseek-v4-flash",
                        default_effort="high",
                        supported_efforts=("low", "medium", "high", "xhigh"),
                    ),
                ),
                "catalog_request_identity": fetched.request_identity,
            }
        ),
    )

    launch = service.resolve_runtime_launch(
        ready.id, model="deepseek-v4-flash", effort="high"
    )
    overrides = launch.codex_overrides()
    provider = overrides["model_providers"][f"trowel_{ready.id.replace('-', '_')}"]

    assert provider["env_key"] == "TROWEL_CODEX_PROVIDER_KEY"
    assert provider["requires_openai_auth"] is False
    assert launch.codex_config_dir == str(managed_root / ready.id)
    assert Path(launch.codex_config_dir).is_dir()
    assert "deepseek-canary-secret" not in repr(overrides)
    assert launch.pool_key == launch.pool_key

    options = service.list_agent_connection_options()
    option = next(item for item in options if item["id"] == ready.id)
    assert option["available"] is True
    assert option["models"] == [
        {
            "id": "deepseek-v4-flash",
            "display_name": None,
            "available": True,
            "disabled_reason": None,
            "efforts": ["low", "medium", "high", "xhigh"],
            "default_effort": "high",
        }
    ]


@pytest.mark.asyncio
async def test_codex_agent_options_follow_saved_selection_order() -> None:
    """新会话只使用用户保存的模型顺序，候选刷新不能重新排序或自动加模型。"""

    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(
                FetchedModel(id="codex-auto-review"),
                FetchedModel(id="gpt-5.6-terra"),
                FetchedModel(id="gpt-5.6-sol"),
                FetchedModel(id="gpt-image-1"),
            ),
            source_endpoint="https://provider.example/v1/models",
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
    fixture = Path(__file__).parents[1] / "codex_host/fixtures/model-list-0.144.0.json"
    native_models, _cursor = parse_model_list_page(
        json.loads(fixture.read_text(encoding="utf-8"))
    )
    selected = service.update_connection(
        created.id,
        expected_version=fetched.connection_version,
        draft=ConnectionDraft(
            **{
                **_deepseek_codex().__dict__,
                "codex_catalog": (
                    CodexCatalogEntry(
                        id="gpt-5.6-terra",
                        default_effort="medium",
                        supported_efforts=("medium", "high"),
                    ),
                    CodexCatalogEntry(
                        id="gpt-5.6-sol",
                        default_effort="high",
                        supported_efforts=("high",),
                    ),
                ),
                "catalog_request_identity": fetched.request_identity,
            }
        ),
    )

    assert native_models
    option = service.list_agent_connection_options()[0]

    assert [model["id"] for model in option["models"]] == [
        "gpt-5.6-terra",
        "gpt-5.6-sol",
    ]
    assert option["models"][0]["efforts"] == ["medium", "high"]
    assert option["models"][0]["default_effort"] == "medium"
    assert all(model["available"] for model in option["models"])
    launch = service.resolve_runtime_launch(
        selected.id,
        model="gpt-5.6-sol",
        effort="high",
    )
    assert launch.effort == "high"


def _deepseek_codex() -> ConnectionDraft:
    """返回已通过真实运行验证的 Codex Responses 连接草稿。"""

    return ConnectionDraft(
        name="DeepSeek A",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        base_url="https://api.deepseek.com/v1",
    )


def test_codex_official_allocates_hidden_account_slot(tmp_path: Path) -> None:
    """Official 供应商自动分配账号槽位，客户端既不填写也不读取底层目录。"""

    service, repository = build_service(official_account_root=tmp_path / "accounts")
    draft = ConnectionDraft(
        name="OpenAI Pro x20",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_OFFICIAL,
        protocol=ProtocolKind.CODEX_OFFICIAL,
    )

    created = service.create_connection(draft)
    row = repository.get_connection(created.id)

    assert created.login_directory is None
    assert created.auth.status == "missing"
    assert row is not None
    slot = Path(str(row["login_directory"]))
    assert slot.parent == tmp_path / "accounts"
    assert slot.is_dir()

    updated = service.update_connection(
        created.id,
        expected_version=created.version,
        draft=ConnectionDraft(**{**draft.__dict__, "name": "工作账号"}),
    )
    updated_row = repository.get_connection(created.id)
    assert updated.name == "工作账号"
    assert updated_row is not None
    assert updated_row["login_directory"] == str(slot)


def test_ready_official_catalog_without_saved_model_has_specific_agent_reason(
    tmp_path: Path,
) -> None:
    """已登录且候选就绪时应明确提示尚未选择模型，而不是误报目录未就绪。"""

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
    service.record_native_codex_catalog(
        created.id,
        expected_version=created.version,
        native_models=({"id": "gpt-5.6-terra"},),
    )

    option = service.list_agent_connection_options()[0]

    assert option["available"] is False
    assert option["disabled_reason"] == "model_not_selected"


def test_ready_official_catalog_with_stale_selection_has_specific_agent_reason(
    tmp_path: Path,
) -> None:
    """已选模型退出最新目录时应引导重新选择，而不是误报目录未就绪。"""

    service, repository = build_service(official_account_root=tmp_path / "accounts")
    draft = ConnectionDraft(
        name="Codex",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_OFFICIAL,
        protocol=ProtocolKind.CODEX_OFFICIAL,
    )
    created = service.create_connection(draft)
    row = repository.get_connection(created.id)
    assert row is not None
    (Path(str(row["login_directory"])) / "auth.json").write_text(
        "oauth-canary", encoding="utf-8"
    )
    first_catalog = service.record_native_codex_catalog(
        created.id,
        expected_version=created.version,
        native_models=({"id": "gpt-old"},),
    )
    selected = service.update_connection(
        created.id,
        expected_version=first_catalog.connection_version,
        draft=ConnectionDraft(
            **{
                **draft.__dict__,
                "codex_catalog": (CodexCatalogEntry(id="gpt-old"),),
                "catalog_request_identity": first_catalog.request_identity,
            }
        ),
    )
    service.record_native_codex_catalog(
        created.id,
        expected_version=selected.version,
        native_models=({"id": "gpt-new"},),
    )

    option = service.list_agent_connection_options()[0]

    assert option["available"] is False
    assert option["disabled_reason"] == "model_selection_stale"


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
async def test_name_only_update_preserves_current_catalog_without_hidden_token() -> (
    None
):
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
async def test_codex_runtime_launch_defers_effort_compatibility_to_codex() -> None:
    """交互会话只校验 catalog，具体 effort 由 Codex 原生协议裁决。"""

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
    await service.fetch_models(created.id, expected_version=with_secret.version)

    launch = service.resolve_runtime_launch(
        created.id,
        model="deepseek-v4-flash",
        effort=None,
    )

    assert launch.model == "deepseek-v4-flash"
    assert launch.effort is None


def test_third_party_codex_keeps_task_gate_without_blocking_interactive_effort() -> (
    None
):
    """交互 effort 交给 Codex，后台任务资格仍只来自实测记录。"""

    from trowel_py.configuration.capabilities import capability_for

    verified = capability_for(
        RuntimeKind.CODEX,
        ConnectionKind.CODEX_CUSTOM,
        ProtocolKind.OPENAI_RESPONSES,
        "gpt-5.6-sol",
        "high",
    )
    unknown_effort = capability_for(
        RuntimeKind.CODEX,
        ConnectionKind.CODEX_CUSTOM,
        ProtocolKind.OPENAI_RESPONSES,
        "gpt-5.6-sol",
        "xhigh",
    )

    assert verified.status == "verified"
    assert verified.version == "provider-runtime-capabilities-v3"
    assert verified.eligible_tasks == ()
    assert unknown_effort.status == "verified"
    assert unknown_effort.eligible_tasks == ()


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


@pytest.mark.asyncio
async def test_direct_api_configuration_cannot_be_saved_as_agent_default() -> None:
    """direct API 只服务后台任务，不能冒充可创建 Agent 会话的 runtime。"""

    fetcher = FakeCatalogFetcher(
        FetchedCatalog(
            models=(FetchedModel(id="glm-5.2"),),
            source_endpoint="https://open.bigmodel.cn/api/anthropic/v1/models",
        )
    )
    service, _repository = build_service(fetcher=fetcher)
    connection = service.create_connection(
        ConnectionDraft(
            name="GLM Weekly direct",
            runtime=RuntimeKind.DIRECT_API,
            kind=ConnectionKind.DIRECT_API,
            protocol=ProtocolKind.ANTHROPIC_MESSAGES,
            base_url="https://open.bigmodel.cn/api/anthropic",
        )
    )
    with_secret = service.write_secret(
        connection.id,
        expected_version=connection.version,
        kind=SecretKind.API_KEY,
        value="test-key",
    )
    fetched = await service.fetch_models(
        connection.id,
        expected_version=with_secret.version,
    )
    configuration = service.create_session_configuration(
        SessionConfigurationDraft(
            name="GLM direct Weekly",
            connection_id=connection.id,
            model="glm-5.2",
        ),
        expected_connection_version=fetched.connection_version,
    )

    with pytest.raises(ConfigurationError) as raised:
        service.put_agent_defaults(
            expected_version=0,
            session_configuration_id=configuration.id,
            permission=None,
            memory_enabled=True,
            profile_enabled=True,
            self_enabled=True,
        )

    assert raised.value.code == "AGENT_RUNTIME_REQUIRED"


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
