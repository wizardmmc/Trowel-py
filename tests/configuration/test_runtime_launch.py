"""验证秘密启动材料只落在应用自有私有目录并可在重启时清理。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from trowel_py.configuration.models import (
    CodexCatalogEntry,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
)
from trowel_py.configuration.runtime_launch import (
    RuntimeLaunchConfiguration,
    cleanup_private_claude_settings,
    write_private_claude_settings,
)


def _launch_with_proxy(proxy_url: str | None) -> RuntimeLaunchConfiguration:
    """构造带认证代理的秘密启动配置，用于泄露 canary。"""

    return RuntimeLaunchConfiguration(
        connection_id="codex-a",
        connection_version=1,
        connection_identity_version=2,
        connection_name="Codex A",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        model="deepseek-v4-flash",
        effort="high",
        capability_version="test",
        base_url="https://provider.example/v1",
        login_directory=None,
        proxy_url=proxy_url,
        claude_role_models={},
        codex_catalog=(),
        api_key="api-canary-secret",
    )


def test_runtime_launch_repr_and_pool_key_exclude_proxy_credentials() -> None:
    """代理和 API 凭据不能进入对象展示，pool identity 也不摄入认证 URL。"""

    launch = _launch_with_proxy("http://alice:proxy-canary-secret@proxy.local:8080")
    rotated_password = replace(
        launch,
        proxy_url="http://alice:rotated-secret@proxy.local:8080",
    )
    changed_endpoint = replace(
        launch,
        proxy_url="http://alice:proxy-canary-secret@other-proxy.local:8080",
    )

    assert "proxy-canary-secret" not in repr(launch)
    assert "api-canary-secret" not in repr(launch)
    assert launch.pool_key == rotated_password.pool_key
    assert launch.pool_key != changed_endpoint.pool_key


def test_codex_pool_key_ignores_session_model_catalog() -> None:
    """只修改用户可选模型时必须继续复用同一条 Codex manager。"""

    launch = _launch_with_proxy(None)
    changed_catalog = replace(
        launch,
        codex_catalog=(
            CodexCatalogEntry(
                id="deepseek-v4-pro",
                default_effort="high",
                supported_efforts=("high",),
            ),
        ),
    )

    assert launch.pool_key == changed_catalog.pool_key


def test_new_codex_home_isolates_discovery_but_restores_shell_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """新版连接把 app-server HOME 隔离到连接家，shell 工具仍看到真实用户家。"""

    real_home = tmp_path / "real-home"
    connection_home = tmp_path / "connection-home"
    monkeypatch.setenv("HOME", str(real_home))
    launch = replace(_launch_with_proxy(None), codex_config_dir=str(connection_home))

    environment = launch.codex_environment(shared_state_root=tmp_path / "shared")
    overrides = launch.codex_overrides()

    assert environment["CODEX_HOME"] == str(connection_home)
    assert environment["HOME"] == str(connection_home)
    assert environment["CODEX_SQLITE_HOME"] == str(tmp_path / "shared")
    assert overrides["shell_environment_policy"] == {
        "set": {"HOME": str(real_home)}
    }


def test_legacy_custom_launch_keeps_shared_codex_home(tmp_path: Path) -> None:
    """没有冻结配置家字段的旧 Custom 会话继续沿用旧共享根。"""

    launch = _launch_with_proxy(None)

    environment = launch.codex_environment(shared_state_root=tmp_path / "shared")

    assert environment["CODEX_HOME"] == str(tmp_path / "shared")
    assert "HOME" not in environment
    assert "shell_environment_policy" not in launch.codex_overrides()


def test_codex_pool_key_includes_frozen_connection_home(tmp_path: Path) -> None:
    """相同 provider 迁移到新配置家后不能复用仍持有旧 HOME 的 manager。"""

    legacy = _launch_with_proxy(None)
    isolated = replace(legacy, codex_config_dir=str(tmp_path / "connection-home"))

    assert isolated.pool_key != legacy.pool_key


def test_private_claude_settings_uses_restricted_owned_directory(
    tmp_path: Path,
) -> None:
    """连接凭据文件及其父目录都必须只允许当前用户访问。"""

    directory = tmp_path / "runtime-private" / "claude-settings"
    path = write_private_claude_settings(
        {"env": {"ANTHROPIC_AUTH_TOKEN": "secret"}},
        directory=directory,
    )

    assert path.parent == directory
    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_private_claude_settings_cleanup_only_removes_owned_pattern(
    tmp_path: Path,
) -> None:
    """启动清理只删除自有命名文件，保留同目录其他诊断材料。"""

    directory = tmp_path / "runtime-private" / "claude-settings"
    stale = write_private_claude_settings(
        {"env": {"ANTHROPIC_AUTH_TOKEN": "secret"}},
        directory=directory,
    )
    unrelated = directory / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")

    cleanup_private_claude_settings(directory)

    assert stale.exists() is False
    assert unrelated.read_text(encoding="utf-8") == "keep"
