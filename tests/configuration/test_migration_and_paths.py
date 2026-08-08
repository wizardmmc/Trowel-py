"""验证旧配置迁移和三种桌面数据模式的真实路径。"""

from __future__ import annotations

from pathlib import Path
import stat

from tests.configuration.support import build_service
from trowel_py.configuration.migration import migrate_legacy_llm_config
from trowel_py.configuration.paths import build_path_status
from trowel_py.configuration.models import SecretKind
from trowel_py.db.connection import create_db


def test_legacy_llm_migration_is_atomic_and_idempotent(tmp_path: Path) -> None:
    service, repository = build_service()
    config = tmp_path / "config.toml"
    config.write_text(
        """
[llm]
active = "legacy-glm"

[llm.legacy-glm]
provider = "anthropic"
model = "glm-5.2"
api_key = "legacy-canary-key"
base_url = "https://open.bigmodel.cn/api/anthropic"
""",
        encoding="utf-8",
    )

    first = migrate_legacy_llm_config(repository, config)
    second = migrate_legacy_llm_config(repository, config)

    assert first.status == "imported"
    assert second.status == "already_imported"
    assert len(service.list_connections()) == 1
    imported = service.list_connections()[0]
    assert imported.name == "legacy-glm"
    assert repository.read_secret(imported.id, SecretKind.API_KEY) == (
        "legacy-canary-key"
    )
    assert "legacy-canary-key" not in str(imported.to_wire())
    assert config.exists()


def test_invalid_legacy_config_leaves_no_partial_rows(tmp_path: Path) -> None:
    service, repository = build_service()
    config = tmp_path / "config.toml"
    config.write_text(
        '[llm]\nactive = "broken"\n[llm.broken]\napi_key = "canary"\n',
        encoding="utf-8",
    )

    result = migrate_legacy_llm_config(repository, config)

    assert result.status == "invalid"
    assert service.list_connections() == ()
    assert repository.count_secrets() == 0


def test_legacy_migration_rolls_back_when_secret_write_fails(tmp_path: Path) -> None:
    service, repository = build_service()
    config = tmp_path / "config.toml"
    config.write_text(
        """
[llm]
active = "legacy-glm"

[llm.legacy-glm]
provider = "anthropic"
model = "glm-5.2"
api_key = "legacy-canary-key"
base_url = "https://open.bigmodel.cn/api/anthropic"
""",
        encoding="utf-8",
    )
    repository.connection.execute(
        """
        CREATE TRIGGER reject_legacy_secret
        BEFORE INSERT ON configuration_secrets
        BEGIN
            SELECT RAISE(ABORT, 'simulated secret storage failure');
        END
        """
    )
    repository.connection.commit()

    result = migrate_legacy_llm_config(repository, config)

    assert result.status == "invalid"
    assert service.list_connections() == ()
    assert repository.count_secrets() == 0


def test_path_status_uses_resolvers_for_each_desktop_mode(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    home = tmp_path / "home"
    data_root.mkdir()
    home.mkdir()
    (data_root / "config.toml").touch()

    for mode in ("packaged", "canonical-dev", "isolated-dev"):
        status = build_path_status(
            environment={
                "TROWEL_DATA_ROOT": str(data_root),
                "TROWEL_DESKTOP_DATA_MODE": mode,
            },
            home=home,
        )
        assert status.data_mode == mode
        assert status.paths["data_root"].path == data_root
        assert status.paths["trowel_config"].exists is True
        assert status.paths["memory"].path == data_root / "memory"
        assert status.paths["profile"].path == data_root / "memory" / "profile.md"
        assert status.paths["connection_registry"].path == data_root / "trowel.db"
        assert status.paths["claude_settings"].path == home / ".claude/settings.json"
        assert status.paths["codex_config"].path == home / ".codex/config.toml"


def test_file_database_is_readable_only_by_current_user(tmp_path: Path) -> None:
    database_path = tmp_path / "private.db"

    connection = create_db(database_path)
    connection.execute("CREATE TABLE private_value (value TEXT NOT NULL)")
    connection.execute("INSERT INTO private_value VALUES ('canary')")
    connection.commit()

    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    for suffix in ("-wal", "-shm"):
        auxiliary_path = Path(f"{database_path}{suffix}")
        if auxiliary_path.exists():
            assert stat.S_IMODE(auxiliary_path.stat().st_mode) == 0o600
    connection.close()
