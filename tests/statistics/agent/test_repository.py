"""验证 Agent 统计只读读取真实 sessions registry 与原生文件。"""

from datetime import date
from pathlib import Path

import pytest

from trowel_py.agent_host.store import BindingStore
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.statistics.agent.repository import FileAgentObservationReader
from trowel_py.statistics.agent.service import build_agent_statistics
from trowel_py.statistics.window import parse_statistics_window

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_file_reader_combines_registry_sources_without_exposing_private_fields(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    cc_transcript = FIXTURES / "cc-binding-2.1.197.jsonl"
    codex_journal = FIXTURES / "codex-turn-0.144.0.jsonl"
    connection = open_sessions_db(memory_root)
    repository = create_sessions_repository(connection)
    repository.claude.register(
        SessionRecord(
            cc_session_id="native-cc-private",
            trowel_session_id="trowel-cc",
            workdir="/private/workspace",
            date="2026-07-14",
            jsonl_path=str(cc_transcript),
            registered_at="2026-07-14T18:45:31.582+08:00",
        )
    )
    repository.claude.update_completed(
        "native-cc-private",
        cc_transcript.stat().st_size,
        "2026-07-14T18:48:53.012+08:00",
    )
    repository.claude.update_binding_status(
        "trowel-cc",
        status="completed",
        completed_at="2026-07-14T18:48:53.012+08:00",
    )
    repository.codex.register_turn(
        thread_id="native-thread-private",
        turn_id="native-turn-private",
        trowel_session_id="trowel-codex",
        workdir="/private/workspace",
        journal_path=str(codex_journal),
        registered_at="2026-07-24T18:48:37.676812+08:00",
        model="gpt-5.6-sol",
        effort="xhigh",
        provider="openai",
        memory_enabled=True,
        profile_enabled=True,
    )
    repository.codex.complete_turn(
        "native-thread-private",
        "native-turn-private",
        status="completed",
        completed_at="2026-07-24T18:50:01.339278+08:00",
    )
    connection.close()
    reader = FileAgentObservationReader(
        memory_root,
        BindingStore(tmp_path / "agent_sessions.json"),
    )

    july = parse_statistics_window(date(2026, 7, 1), date(2026, 7, 31), "UTC")
    result = build_agent_statistics(reader, july)

    assert result.sample_size == 2
    assert {row.runtime for row in result.sessions} == {"claude_code", "codex"}
    rendered = result.model_dump_json()
    assert "native-cc-private" not in rendered
    assert "native-thread-private" not in rendered
    assert "/private/workspace" not in rendered


def test_file_reader_does_not_send_out_of_window_files_to_adapters(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """仓储先用登记时间筛选，避免为小时间窗扫描全部历史日志。"""

    memory_root = tmp_path / "memory"
    connection = open_sessions_db(memory_root)
    repository = create_sessions_repository(connection)
    for suffix, registered_at, completed_at in (
        ("outside", "2026-07-01T10:00:00+08:00", "2026-07-01T10:05:00+08:00"),
        ("inside", "2026-08-03T10:00:00+08:00", "2026-08-03T10:05:00+08:00"),
    ):
        repository.claude.register(
            SessionRecord(
                cc_session_id=f"cc-{suffix}",
                trowel_session_id=f"trowel-cc-{suffix}",
                workdir="/private/workspace",
                date=registered_at[:10],
                jsonl_path=str(tmp_path / f"cc-{suffix}.jsonl"),
                registered_at=registered_at,
            )
        )
        repository.claude.update_binding_status(
            f"trowel-cc-{suffix}",
            status="completed",
            completed_at=completed_at,
        )
        repository.codex.register_turn(
            thread_id=f"thread-{suffix}",
            turn_id=f"turn-{suffix}",
            trowel_session_id=f"trowel-codex-{suffix}",
            workdir="/private/workspace",
            journal_path=str(tmp_path / f"codex-{suffix}.jsonl"),
            registered_at=registered_at,
            model="model",
            effort="high",
            provider="provider",
            memory_enabled=True,
            profile_enabled=True,
        )
        repository.codex.complete_turn(
            f"thread-{suffix}",
            f"turn-{suffix}",
            status="completed",
            completed_at=completed_at,
        )
    connection.close()
    analyzed: list[str] = []

    def capture_claude(source, _window):
        """记录被交给 Claude adapter 的 Trowel session。"""

        analyzed.append(source.session_id)
        return None

    def capture_codex(sources, _window):
        """记录被交给 Codex adapter 的 Trowel session。"""

        analyzed.extend(source.session_id for source in sources)
        return None

    monkeypatch.setattr(
        "trowel_py.statistics.agent.repository.analyze_claude_binding",
        capture_claude,
    )
    monkeypatch.setattr(
        "trowel_py.statistics.agent.repository.analyze_codex_session",
        capture_codex,
    )
    reader = FileAgentObservationReader(
        memory_root,
        BindingStore(tmp_path / "agent_sessions.json"),
    )
    window = parse_statistics_window(date(2026, 8, 3), date(2026, 8, 3), "UTC")

    reader.read(window)

    assert analyzed == ["trowel-cc-inside", "trowel-codex-inside"]
