"""验证 Claude 连接家的隔离、覆盖式继承和删除保留语义。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.configuration.claude_home import (
    ClaudeConnectionHomeError,
    ClaudeConnectionHomeStore,
)


def _store(tmp_path: Path) -> ClaudeConnectionHomeStore:
    """构造全局源与托管根都位于临时目录的真实文件存储。"""

    return ClaudeConnectionHomeStore(
        tmp_path / "managed",
        global_home=tmp_path / "global" / ".claude",
    )


def test_new_connection_home_is_blank_and_private(tmp_path: Path) -> None:
    """新连接只创建私有根，不会暗中复制真实全局配置。"""

    store = _store(tmp_path)
    connection_id = "11111111-1111-4111-8111-111111111111"
    (store.global_home / "skills" / "global-skill").mkdir(parents=True)
    home = store.ensure_home(connection_id)

    assert list(home.iterdir()) == []
    assert home.stat().st_mode & 0o777 == 0o700
    assert store.is_inherited(connection_id) is False


def test_inherit_copies_supported_config_and_removes_settings_env(
    tmp_path: Path,
) -> None:
    """继承保留 hooks/权限等非凭据语义，但整块删除 provider env。"""

    store = _store(tmp_path)
    connection_id = "22222222-2222-4222-8222-222222222222"
    skill = store.global_home / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: review\n---\n", encoding="utf-8")
    (store.global_home / "commands").mkdir()
    (store.global_home / "commands" / "ship.md").write_text("ship", encoding="utf-8")
    (store.global_home / "CLAUDE.md").write_text("global rules", encoding="utf-8")
    (store.global_home / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "known-provider-secret",
                    "CANARY": "must-not-copy",
                },
                "hooks": {"PreToolUse": [{"matcher": "Bash"}]},
                "permissions": {"allow": ["Read"]},
            }
        ),
        encoding="utf-8",
    )
    (store.global_home / ".claude.json").write_text("private state", encoding="utf-8")
    (store.global_home / "projects").mkdir()
    (store.global_home / "plugins").mkdir()

    result = store.inherit_global(connection_id)
    home = store.home_for(connection_id)
    inherited_settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))

    assert result.inherited is True
    assert inherited_settings == {
        "hooks": {"PreToolUse": [{"matcher": "Bash"}]},
        "permissions": {"allow": ["Read"]},
    }
    assert (home / "skills" / "review" / "SKILL.md").is_file()
    assert (home / "commands" / "ship.md").is_file()
    assert (home / "CLAUDE.md").read_text(encoding="utf-8") == "global rules"
    assert not (home / ".claude.json").exists()
    assert not (home / "projects").exists()
    assert not (home / "plugins").exists()
    assert store.shared_plugin_root == store.global_home / "plugins"
    assert store.is_inherited(connection_id) is True


def test_failed_reinherit_preserves_previous_published_copy(tmp_path: Path) -> None:
    """源 settings 损坏时不能留下一半新、一半旧的连接家。"""

    store = _store(tmp_path)
    connection_id = "33333333-3333-4333-8333-333333333333"
    store.global_home.mkdir(parents=True)
    settings = store.global_home / "settings.json"
    settings.write_text('{"permissions":{"allow":["Read"]}}', encoding="utf-8")
    store.inherit_global(connection_id)
    published = store.home_for(connection_id) / "settings.json"
    before = published.read_bytes()
    settings.write_text("{not-json", encoding="utf-8")

    with pytest.raises(ClaudeConnectionHomeError):
        store.inherit_global(connection_id)

    assert published.read_bytes() == before
    assert store.is_inherited(connection_id) is True


def test_inherit_rejects_known_secret_outside_settings_env(tmp_path: Path) -> None:
    """已知 provider 凭据即使出现在 CLAUDE.md，也不能被 env 剔除逻辑漏过。"""

    store = _store(tmp_path)
    connection_id = "55555555-5555-4555-8555-555555555555"
    store.global_home.mkdir(parents=True)
    # 短凭据同样属于 Trowel 已知 secret，不能为了减少误报而跳过。
    secret = "k3y"
    (store.global_home / "CLAUDE.md").write_text(
        f"accidentally copied: {secret}",
        encoding="utf-8",
    )

    with pytest.raises(ClaudeConnectionHomeError, match="已知凭据"):
        store.inherit_global(connection_id, forbidden_values=(secret,))

    assert store.is_inherited(connection_id) is False


def test_inherit_materializes_source_symlinks_before_scanning(tmp_path: Path) -> None:
    """常见的共享 skill 链接应按当前内容复制，发布结果不能保留链接。"""

    store = _store(tmp_path)
    connection_id = "66666666-6666-4666-8666-666666666666"
    shared_skill = tmp_path / "shared" / "review"
    shared_skill.mkdir(parents=True)
    (shared_skill / "SKILL.md").write_text("shared skill", encoding="utf-8")
    skill = store.global_home / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").symlink_to(shared_skill / "SKILL.md")

    store.inherit_global(connection_id)

    inherited = store.home_for(connection_id) / "skills" / "review" / "SKILL.md"
    assert inherited.read_text(encoding="utf-8") == "shared skill"
    assert not inherited.is_symlink()


def test_deletion_keeps_history_tombstone_and_can_restore(tmp_path: Path) -> None:
    """删除只把连接家移到保留墓碑，历史扫描仍可发现 projects。"""

    store = _store(tmp_path)
    connection_id = "44444444-4444-4444-8444-444444444444"
    home = store.ensure_home(connection_id)
    projects = home / "projects"
    projects.mkdir()

    deletion = store.stage_deletion(connection_id)

    assert deletion is not None
    assert home.is_dir()
    assert deletion.tombstone.is_file()
    assert projects in store.projects_roots()

    store.restore_deletion(deletion)

    assert projects.is_dir()
    assert not deletion.tombstone.exists()
