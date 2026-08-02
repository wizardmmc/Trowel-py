"""验证旧开发数据一次性迁入正式 App 根目录且不导入旧 Garden。"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from trowel_py.desktop import data_migration
from trowel_py.desktop.data_migration import (
    DesktopDataMigrationError,
    apply_desktop_data_migration,
    plan_desktop_data_migration,
)
from trowel_py.memory.sessions_repo.database import initialize_schema
from trowel_py.resource_lifecycle.processes import LocalProcessController


def _write_titles(path: Path, records: dict[str, dict[str, dict[str, str]]]) -> None:
    path.write_text(
        json.dumps({"version": 1, "titles": records}), encoding="utf-8"
    )


def _write_non_user_identities(
    path: Path,
    *,
    claude_code: list[str],
    codex: list[str],
) -> None:
    """写入迁移前兼容格式的非用户原生会话索引。"""

    path.write_text(
        json.dumps(
            {
                "version": 1,
                "native_session_ids": {
                    "claude_code": claude_code,
                    "codex": codex,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_workspace(path: Path, workspace: str, opened_at: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE recent_workspaces("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "path TEXT NOT NULL UNIQUE, opened_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO recent_workspaces(path, opened_at) VALUES (?, ?)",
        (workspace, opened_at),
    )
    connection.commit()
    connection.close()


def _write_turn(memory_root: Path, thread_id: str, turn_id: str) -> Path:
    journal = memory_root / "meta" / "codex-turns" / thread_id / f"{turn_id}.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text('{"type":"finished"}\n', encoding="utf-8")
    connection = sqlite3.connect(memory_root / "meta" / "sessions.db")
    connection.row_factory = sqlite3.Row
    initialize_schema(connection)
    connection.execute(
        "INSERT INTO codex_turns("
        "thread_id, turn_id, trowel_session_id, workdir, journal_path,"
        "registered_at, status, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            thread_id,
            turn_id,
            f"trowel-{thread_id}",
            "/workspace",
            str(journal),
            "2026-08-02T00:00:00",
            "completed",
            "2026-08-02T00:01:00",
        ),
    )
    connection.commit()
    connection.close()
    return journal


def _fixture_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    legacy = tmp_path / ".trowel"
    previous_app = tmp_path / "Trowel"
    target = previous_app / "data"
    config = tmp_path / "config.toml"
    legacy_memory = legacy / "memory"
    current_memory = previous_app / "memory"
    for root in (legacy, previous_app, legacy_memory, current_memory):
        root.mkdir(parents=True, exist_ok=True)

    (legacy_memory / "notes").mkdir()
    (legacy_memory / "notes" / "knowledge.md").write_text(
        "legacy note", encoding="utf-8"
    )
    (legacy_memory / "profile.md").write_text("# 用户画像\n长期画像", encoding="utf-8")
    (legacy_memory / "dictionary-L0.md").write_text("- memory", encoding="utf-8")
    legacy_journal = _write_turn(legacy_memory, "legacy-thread", "legacy-turn")
    (legacy_memory / "episodes").mkdir()
    (legacy_memory / "episodes" / "legacy-thread.md").write_text(
        "---\n"
        "type: episode\n"
        f"source_jsonl: {legacy_journal}\n"
        "---\n"
        "episode body\n",
        encoding="utf-8",
    )
    _write_turn(current_memory, "current-thread", "current-turn")

    _write_titles(
        legacy / "agent_session_titles.json",
        {
            "codex": {
                "shared": {
                    "title": "长期标题",
                    "source": "generated",
                    "updated_at": "2026-08-01T00:00:00",
                }
            }
        },
    )
    _write_titles(
        previous_app / "agent_session_titles.json",
        {
            "codex": {
                "shared": {
                    "title": "原生回退标题",
                    "source": "native",
                    "updated_at": "2026-08-02T00:00:00",
                },
                "current": {
                    "title": "当前会话",
                    "source": "native",
                    "updated_at": "2026-08-02T00:00:00",
                },
            }
        },
    )
    _write_workspace(legacy / "workspaces.db", "/legacy", "2026-08-01T00:00:00")
    _write_workspace(
        previous_app / "workspaces.db", "/current", "2026-08-02T00:00:00"
    )
    (previous_app / "agent_sessions.json").write_text(
        '{"version":1,"sessions":{"current":{}}}', encoding="utf-8"
    )
    _write_non_user_identities(
        legacy / "agent_sessions.delegates.json",
        claude_code=["legacy-cc-review"],
        codex=["shared-review", "legacy-codex-review"],
    )
    _write_non_user_identities(
        previous_app / "agent_sessions.delegates.json",
        claude_code=["current-cc-review"],
        codex=["shared-review", "current-codex-review"],
    )

    current_db = sqlite3.connect(previous_app / "trowel.db")
    current_db.execute("CREATE TABLE app_marker(value TEXT)")
    current_db.execute("INSERT INTO app_marker(value) VALUES ('current-app')")
    current_db.commit()
    current_db.close()
    app_instance_id = "candidate-instance"
    (previous_app / "resource-lifecycle.json").write_text(
        json.dumps(
            {
                "version": 1,
                "app_instance_id": app_instance_id,
                "resources": [],
            }
        ),
        encoding="utf-8",
    )
    (previous_app / "resource-exit.json").write_text(
        json.dumps(
            {
                "version": 1,
                "app_instance_id": app_instance_id,
                "status": "closed",
                "remaining_resource_count": 0,
            }
        ),
        encoding="utf-8",
    )
    old_garden = sqlite3.connect(legacy / "trowel.db")
    old_garden.execute("CREATE TABLE app_marker(value TEXT)")
    old_garden.execute("INSERT INTO app_marker(value) VALUES ('old-garden')")
    old_garden.commit()
    old_garden.close()

    config.write_text("[llm]\nactive='local'\n", encoding="utf-8")
    return legacy, previous_app, target, config


def test_plan_is_read_only_and_excludes_old_garden(tmp_path: Path) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)

    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )

    assert report.ready
    assert report.legacy_notes == 1
    assert report.legacy_codex_turns == 1
    assert report.current_codex_turns == 1
    assert report.migrate_old_garden is False
    assert not target.exists()


def test_plan_refuses_apply_while_previous_app_resource_is_alive(
    tmp_path: Path,
) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    identity = LocalProcessController().inspect(os.getpid())
    assert identity is not None
    (previous_app / "resource-lifecycle.json").write_text(
        json.dumps(
            {
                "version": 1,
                "app_instance_id": "candidate-instance",
                "resources": [
                    {
                        "state": "running",
                        "pid": identity.pid,
                        "process_group": identity.process_group,
                        "process_start_identity": identity.start_identity,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )

    assert not report.ready
    assert any("quit Trowel first" in blocker for blocker in report.blockers)


def test_plan_requires_a_clean_exit_from_the_current_app_instance(
    tmp_path: Path,
) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    (previous_app / "resource-lifecycle.json").write_text(
        json.dumps(
            {
                "version": 1,
                "app_instance_id": "still-running-instance",
                "resources": [],
            }
        ),
        encoding="utf-8",
    )

    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )

    assert not report.ready
    assert any("matching clean exit" in blocker for blocker in report.blockers)


def test_apply_merges_memory_titles_and_workspaces_without_old_garden(
    tmp_path: Path,
) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )

    result = apply_desktop_data_migration(report)

    assert result.status == "completed"
    assert (target / "memory" / "profile.md").read_text(encoding="utf-8").endswith(
        "长期画像"
    )
    assert (target / "memory" / "notes" / "knowledge.md").exists()
    connection = sqlite3.connect(target / "memory" / "meta" / "sessions.db")
    rows = connection.execute(
        "SELECT thread_id, journal_path FROM codex_turns ORDER BY thread_id"
    ).fetchall()
    connection.close()
    assert [row[0] for row in rows] == ["current-thread", "legacy-thread"]
    assert all(str(target / "memory") in row[1] for row in rows)

    episode = (target / "memory" / "episodes" / "legacy-thread.md").read_text(
        encoding="utf-8"
    )
    assert f"source_jsonl: {target / 'memory'}" in episode
    titles = json.loads(
        (target / "agent_session_titles.json").read_text(encoding="utf-8")
    )["titles"]["codex"]
    assert titles["shared"]["title"] == "长期标题"
    assert titles["current"]["title"] == "当前会话"
    connection = sqlite3.connect(target / "workspaces.db")
    workspaces = {
        row[0] for row in connection.execute("SELECT path FROM recent_workspaces")
    }
    connection.close()
    assert workspaces == {"/legacy", "/current"}
    identities = json.loads(
        (target / "agent_sessions.delegates.json").read_text(encoding="utf-8")
    )["native_session_ids"]
    assert identities == {
        "claude_code": ["current-cc-review", "legacy-cc-review"],
        "codex": [
            "current-codex-review",
            "legacy-codex-review",
            "shared-review",
        ],
    }

    connection = sqlite3.connect(target / "trowel.db")
    marker = connection.execute("SELECT value FROM app_marker").fetchone()[0]
    connection.close()
    assert marker == "current-app"
    assert stat.S_IMODE((target / "config.toml").stat().st_mode) == 0o600
    assert (target / "migration-manifest.json").exists()


def test_apply_keeps_more_advanced_legacy_copy_of_overlapping_turn(
    tmp_path: Path,
) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    current_memory = previous_app / "memory"
    duplicate = _write_turn(current_memory, "legacy-thread", "legacy-turn")
    duplicate.write_text('{"type":"turn_start"}\n', encoding="utf-8")
    connection = sqlite3.connect(current_memory / "meta" / "sessions.db")
    connection.execute(
        "UPDATE codex_turns SET status='running', completed_at=NULL "
        "WHERE thread_id='legacy-thread' AND turn_id='legacy-turn'"
    )
    connection.commit()
    connection.close()

    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )
    assert report.overlapping_codex_turns == 1

    apply_desktop_data_migration(report)

    connection = sqlite3.connect(target / "memory" / "meta" / "sessions.db")
    row = connection.execute(
        "SELECT status, completed_at, journal_path FROM codex_turns "
        "WHERE thread_id='legacy-thread' AND turn_id='legacy-turn'"
    ).fetchone()
    count = connection.execute("SELECT COUNT(*) FROM codex_turns").fetchone()[0]
    connection.close()
    assert count == 2
    assert row[0] == "completed"
    assert row[1] is not None
    assert Path(row[2]).read_text(encoding="utf-8") == '{"type":"finished"}\n'


def test_apply_uses_refreshed_turn_counts_after_acquiring_lock(tmp_path: Path) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )
    _write_turn(previous_app / "memory", "late-thread", "late-turn")

    apply_desktop_data_migration(report)

    connection = sqlite3.connect(target / "memory" / "meta" / "sessions.db")
    count = connection.execute("SELECT COUNT(*) FROM codex_turns").fetchone()[0]
    connection.close()
    assert count == 3


def test_apply_reports_a_corrupt_app_database_as_a_migration_error(
    tmp_path: Path,
) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    (previous_app / "trowel.db").write_bytes(b"not a sqlite database")
    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )

    with pytest.raises(DesktopDataMigrationError, match="SQLite snapshot failed"):
        apply_desktop_data_migration(report)


def test_apply_rechecks_that_the_previous_app_did_not_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy, previous_app, target, config = _fixture_roots(tmp_path)
    report = plan_desktop_data_migration(
        target_root=target,
        legacy_root=legacy,
        previous_app_root=previous_app,
        config_source=config,
    )
    copy_app_state = data_migration._copy_current_app_state

    def copy_then_start_app(plan, stage):
        copy_app_state(plan, stage)
        (previous_app / "resource-lifecycle.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "app_instance_id": "restarted-instance",
                    "resources": [],
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(data_migration, "_copy_current_app_state", copy_then_start_app)

    with pytest.raises(DesktopDataMigrationError, match="changed during migration"):
        apply_desktop_data_migration(report)
    assert not target.exists()
