"""验证 Codex 连接家的双来源继承、隔离和失败回滚。"""

from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.configuration.codex_home import (
    CodexConnectionHomeError,
    CodexConnectionHomeStore,
)


def _store(tmp_path: Path) -> CodexConnectionHomeStore:
    """构造不会读取用户真实 Codex 或 Agents 配置的文件存储。"""

    return CodexConnectionHomeStore(
        tmp_path / "managed",
        global_codex_home=tmp_path / "global" / ".codex",
        global_agents_home=tmp_path / "global" / ".agents",
    )


def test_new_codex_home_is_blank_and_private(tmp_path: Path) -> None:
    """创建连接家不能暗中复制任一全局配置来源。"""

    store = _store(tmp_path)
    connection_id = "11111111-1111-4111-8111-111111111111"
    (store.global_agents_home / "skills" / "global-skill").mkdir(parents=True)

    home = store.ensure_home(connection_id)

    assert list(home.iterdir()) == []
    assert home.stat().st_mode & 0o777 == 0o700
    assert store.is_inherited(connection_id) is False


def test_inherit_copies_only_supported_codex_and_agents_config(
    tmp_path: Path,
) -> None:
    """继承配置入口但排除认证、会话、数据库和插件物理缓存。"""

    store = _store(tmp_path)
    connection_id = "22222222-2222-4222-8222-222222222222"
    legacy_skill = store.global_codex_home / "skills" / "legacy"
    legacy_skill.mkdir(parents=True)
    (legacy_skill / "SKILL.md").write_text("legacy", encoding="utf-8")
    rule = store.global_codex_home / "rules"
    rule.mkdir()
    (rule / "default.rules").write_text("allow", encoding="utf-8")
    store.global_codex_home.joinpath("AGENTS.md").write_text(
        "global instructions",
        encoding="utf-8",
    )
    store.global_codex_home.joinpath("config.toml").write_text(
        'model = "gpt-example"\n',
        encoding="utf-8",
    )
    store.global_codex_home.joinpath("auth.json").write_text(
        "must stay global",
        encoding="utf-8",
    )
    store.global_codex_home.joinpath("sessions").mkdir()
    store.global_codex_home.joinpath("plugins").mkdir()
    agents_skill = store.global_agents_home / "skills" / "shared"
    agents_skill.mkdir(parents=True)
    (agents_skill / "SKILL.md").write_text("shared", encoding="utf-8")

    result = store.inherit_global(connection_id)
    home = store.home_for(connection_id)

    assert result.inherited is True
    assert result.agents_skill_count == 1
    assert (home / "skills" / "legacy" / "SKILL.md").is_file()
    assert (home / ".agents" / "skills" / "shared" / "SKILL.md").is_file()
    assert (home / "AGENTS.md").read_text(encoding="utf-8") == "global instructions"
    assert not (home / "auth.json").exists()
    assert not (home / "sessions").exists()
    assert not (home / "plugins").exists()
    assert store.is_inherited(connection_id) is True


def test_reinherit_replaces_managed_config_but_preserves_account_state(
    tmp_path: Path,
) -> None:
    """再次继承覆盖配置副本，同时保留连接自己的 OAuth 和原生状态。"""

    store = _store(tmp_path)
    connection_id = "33333333-3333-4333-8333-333333333333"
    source = store.global_codex_home / "AGENTS.md"
    source.parent.mkdir(parents=True)
    source.write_text("first", encoding="utf-8")
    store.inherit_global(connection_id)
    home = store.home_for(connection_id)
    (home / "auth.json").write_text("connection auth", encoding="utf-8")
    (home / ".agents" / "connection-state").mkdir(parents=True)
    (home / ".agents" / "connection-state" / "local.json").write_text(
        "private state",
        encoding="utf-8",
    )
    source.write_text("second", encoding="utf-8")

    store.inherit_global(connection_id)

    assert (home / "AGENTS.md").read_text(encoding="utf-8") == "second"
    assert (home / "auth.json").read_text(encoding="utf-8") == "connection auth"
    assert (
        home / ".agents" / "connection-state" / "local.json"
    ).read_text(encoding="utf-8") == "private state"


def test_failed_reinherit_preserves_previous_published_copy(tmp_path: Path) -> None:
    """来源路径损坏时不能留下半新半旧的配置家。"""

    store = _store(tmp_path)
    connection_id = "44444444-4444-4444-8444-444444444444"
    source = store.global_codex_home / "AGENTS.md"
    source.parent.mkdir(parents=True)
    source.write_text("stable", encoding="utf-8")
    store.inherit_global(connection_id)
    published = store.home_for(connection_id) / "AGENTS.md"
    before = published.read_bytes()
    source.unlink()
    source.mkdir()

    with pytest.raises(CodexConnectionHomeError):
        store.inherit_global(connection_id)

    assert published.read_bytes() == before
    assert store.is_inherited(connection_id) is True


def test_inherit_materializes_symlinks_and_rejects_known_secrets(
    tmp_path: Path,
) -> None:
    """继承物化 skill 链接，并在稳定副本中扫描 Trowel 已知凭据。"""

    store = _store(tmp_path)
    connection_id = "55555555-5555-4555-8555-555555555555"
    shared = tmp_path / "shared" / "skill"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("safe", encoding="utf-8")
    skill = store.global_agents_home / "skills" / "linked"
    skill.parent.mkdir(parents=True)
    skill.symlink_to(shared, target_is_directory=True)

    store.inherit_global(connection_id)
    inherited = store.home_for(connection_id) / ".agents" / "skills" / "linked"

    assert (inherited / "SKILL.md").read_text(encoding="utf-8") == "safe"
    assert not inherited.is_symlink()

    (shared / "SKILL.md").write_text("contains k3y", encoding="utf-8")
    with pytest.raises(CodexConnectionHomeError, match="已知凭据"):
        store.inherit_global(connection_id, forbidden_values=("k3y",))

    assert (inherited / "SKILL.md").read_text(encoding="utf-8") == "safe"


def test_interrupted_directory_swap_retains_recoverable_old_home(
    tmp_path: Path,
) -> None:
    """发布和回滚同时失败时不能清理唯一的旧家恢复副本。"""

    store = _store(tmp_path)
    connection_id = "66666666-6666-4666-8666-666666666666"
    source = store.global_codex_home / "AGENTS.md"
    source.parent.mkdir(parents=True)
    source.write_text("stable", encoding="utf-8")
    store.inherit_global(connection_id)
    home = store.home_for(connection_id)
    source.write_text("replacement", encoding="utf-8")
    original_rename = Path.rename

    def fail_both_swaps(path: Path, target: Path) -> Path:
        """模拟新家发布与旧家就地恢复连续失败。"""

        if target == home and path.name.startswith(
            (".inherit-stage-", ".inherit-backup-")
        ):
            raise OSError("simulated swap failure")
        return original_rename(path, target)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "rename", fail_both_swaps)
        with pytest.raises(CodexConnectionHomeError, match="私有恢复副本"):
            store.inherit_global(connection_id)

    assert not home.exists()
    assert len(list(store.root.glob(f".inherit-backup-{connection_id}-*"))) == 1

    assert store.is_inherited(connection_id) is True
    recovered = store.existing_home(connection_id)

    assert recovered is not None
    assert (recovered / "AGENTS.md").read_text(encoding="utf-8") == "stable"
    assert not list(store.root.glob(f".inherit-backup-{connection_id}-*"))


def test_reinherit_rejects_symlinked_managed_parent_without_touching_target(
    tmp_path: Path,
) -> None:
    """连接家嵌套父目录被替换成链接时，不能沿链接删除外部内容。"""

    store = _store(tmp_path)
    connection_id = "88888888-8888-4888-8888-888888888888"
    home = store.ensure_home(connection_id)
    outside = tmp_path / "outside-agents"
    victim = outside / "skills" / "victim" / "SKILL.md"
    victim.parent.mkdir(parents=True)
    victim.write_text("keep", encoding="utf-8")
    (home / ".agents").symlink_to(outside, target_is_directory=True)
    source = store.global_agents_home / "skills" / "new" / "SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text("new", encoding="utf-8")

    with pytest.raises(CodexConnectionHomeError, match="父路径不能是符号链接"):
        store.inherit_global(connection_id)

    assert victim.read_text(encoding="utf-8") == "keep"
    assert (home / ".agents").is_symlink()
