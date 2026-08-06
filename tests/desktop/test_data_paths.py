"""验证桌面应用只重定向 Trowel 自有数据，不改写 runtime 的用户主目录。"""

import os
from pathlib import Path

from trowel_py.agent_host.store import resolve_bindings_path
from trowel_py.agent_host.workspaces import resolve_recent_workspaces_path
from trowel_py.application_paths import resolve_application_data_root
from trowel_py import config
from trowel_py.db.connection import resolve_database_path
from trowel_py.memory import paths as memory_paths
from trowel_py.memory.mcp_config import _config_path


def test_suite_isolates_parent_desktop_host_environment(tmp_path: Path) -> None:
    """桌面版内运行测试时不得继承正在使用的宿主数据与资源登记路径。"""

    assert Path(os.environ["TROWEL_DATA_ROOT"]) == tmp_path / "trowel-data"
    for variable in (
        "TROWEL_APP_INSTANCE_ID",
        "TROWEL_DESKTOP_CREDENTIAL",
        "TROWEL_DESKTOP_DATA_DIR",
        "TROWEL_DESKTOP_DATA_MODE",
        "TROWEL_DESKTOP_LOG_DIR",
        "TROWEL_DESKTOP_RENDERER_ORIGIN",
        "TROWEL_PROJECT_ROOT",
        "TROWEL_SERVER_PORT",
    ):
        assert variable not in os.environ


def test_desktop_data_root_owns_every_trowel_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """显式数据根目录必须覆盖所有 Trowel 自有的默认持久化路径。"""
    data_root = tmp_path / "application-data"
    workdir = tmp_path / "workspace"
    home = tmp_path / "home"
    data_root.mkdir()
    workdir.mkdir()
    home.mkdir()
    (data_root / "config.toml").write_text(
        '[memory]\nroot = "/must-not-be-used-either"\n',
        encoding="utf-8",
    )
    (workdir / "config.toml").write_text(
        '[memory]\nroot = "/must-not-be-used"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TROWEL_DATA_ROOT", str(data_root))
    monkeypatch.delenv("TROWEL_AGENT_SESSIONS_PATH", raising=False)
    monkeypatch.delenv("TROWEL_WORKSPACES_PATH", raising=False)
    monkeypatch.delenv("TROWEL_MCP_CONFIG", raising=False)
    monkeypatch.delenv("TROWEL_MCP_CONFIG_DIR", raising=False)

    assert resolve_application_data_root() == data_root
    assert resolve_database_path() == data_root / "trowel.db"
    assert memory_paths.find_config_path() == data_root / "config.toml"
    assert memory_paths.resolve_memory_root() == data_root / "memory"
    assert resolve_bindings_path() == data_root / "agent_sessions.json"
    assert resolve_recent_workspaces_path() == data_root / "workspaces.db"
    assert _config_path("session-123") == (
        data_root / "mcp-configs" / "session-123.json"
    )
    assert Path.home() == home


def test_application_lifespan_wires_every_owner_to_desktop_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """真实应用装配必须把 scheduler、Hub 和 workspace 仓储接到同一根目录。"""
    from fastapi.testclient import TestClient

    from trowel_py.app import create_app
    from trowel_py.memory.tidy_scheduler import TidyScheduler

    data_root = tmp_path / "application-data"
    data_root.mkdir()
    (data_root / "config.toml").write_text(
        """[memory]
review_enabled = false
distill_enabled = false

[llm]
active = "test"

[llm.test]
provider = "anthropic"
model = "test-model"
api_key = "test-key"
base_url = "http://127.0.0.1:1"
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(data_root)
    monkeypatch.setenv("TROWEL_DESKTOP_DATA_DIR", str(data_root))
    monkeypatch.setenv("TROWEL_DATA_ROOT", str(data_root))
    monkeypatch.delenv("TROWEL_AGENT_SESSIONS_PATH", raising=False)
    monkeypatch.delenv("TROWEL_WORKSPACES_PATH", raising=False)

    from trowel_py.configuration.models import (
        ConnectionDraft,
        ConnectionKind,
        ProtocolKind,
        RuntimeKind,
    )
    from trowel_py.configuration.repository import ConfigurationRepository
    from trowel_py.configuration.service import ConfigurationService
    from trowel_py.db.connection import create_db
    from trowel_py.db.migrate import run_migrations

    connection = create_db()
    run_migrations(connection)
    repository = ConfigurationRepository(connection)
    service = ConfigurationService(repository)
    official = service.create_connection(
        ConnectionDraft(
            name="历史 Official",
            runtime=RuntimeKind.CODEX,
            kind=ConnectionKind.CODEX_OFFICIAL,
            protocol=ProtocolKind.CODEX_OFFICIAL,
        )
    )
    legacy_slot = data_root / "legacy-shared-codex"
    legacy_slot.mkdir()
    (legacy_slot / "auth.json").write_text("oauth-canary", encoding="utf-8")
    repository.update_connection(
        official.id,
        expected_version=official.version,
        values={
            "version": official.version + 1,
            "login_directory": str(legacy_slot),
        },
    )
    connection.commit()
    connection.close()

    async def skip_tidy_catchup(self: TidyScheduler) -> None:
        """测试只核对装配路径，不执行真实模型补跑。"""

    monkeypatch.setattr(TidyScheduler, "start", skip_tidy_catchup)

    app = create_app()
    with TestClient(app):
        expected_memory = data_root / "memory"
        assert app.state.memory_scheduler._memory_root == expected_memory
        assert app.state.distill_scheduler._memory_root == expected_memory
        assert app.state.tidy_scheduler._memory_root == expected_memory
        assert app.state.agent_hub.store.path == data_root / "agent_sessions.json"
        assert app.state.recent_workspace_store.path == data_root / "workspaces.db"
        connection = create_db()
        migrated = ConfigurationRepository(connection).get_connection(official.id)
        expected_slot = data_root / "codex-accounts" / official.id
        assert migrated is not None
        assert migrated["login_directory"] == str(expected_slot)
        connection.close()
        assert expected_slot.is_dir()
        assert not (expected_slot / "auth.json").exists()
        assert (legacy_slot / "auth.json").read_text(encoding="utf-8") == "oauth-canary"


def test_browser_defaults_still_use_home_without_desktop_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """没有桌面覆盖时继续兼容既有 ``~/.trowel`` 数据布局。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TROWEL_DATA_ROOT", raising=False)
    monkeypatch.delenv("TROWEL_AGENT_SESSIONS_PATH", raising=False)
    monkeypatch.delenv("TROWEL_WORKSPACES_PATH", raising=False)

    expected = home / ".trowel"
    assert resolve_application_data_root() == expected
    assert resolve_bindings_path() == expected / "agent_sessions.json"
    assert resolve_recent_workspaces_path() == expected / "workspaces.db"


def test_llm_config_does_not_fall_back_to_source_or_packaged_module(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """工作目录无配置时返回应用数据候选，不读取仓库或 ``_internal``。"""

    workdir = tmp_path / "workdir"
    home = tmp_path / "home"
    workdir.mkdir()
    home.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TROWEL_DATA_ROOT", raising=False)

    assert config._find_config_path() == home / ".trowel" / "config.toml"
