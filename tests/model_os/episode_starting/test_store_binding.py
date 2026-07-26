from __future__ import annotations

import pytest

from tests.model_os._episode_helpers import make_running_system_episode
from trowel_py.model_os.store import StaleWriterRejected
from trowel_py.model_os.types import EpisodeStatus


def test_runtime_binding_activates_episode_and_replays_from_journal(store) -> None:
    episode, lease, _ = make_running_system_episode(store)

    binding = store.bind_episode_runtime(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        agent_session_id="agent-session-1",
        runtime="codex",
        native_session_id="thread-1",
        runtime_generation="codex-connection-3",
        runtime_pid=123,
        runtime_pgid=123,
        correlation_id="command.start.1",
        activate=True,
    )

    assert binding.native_session_id == "thread-1"
    assert store.read_snapshot().episode_by_id(episode.episode_id).status is EpisodeStatus.ACTIVE
    assert store.episode_runtime_binding(episode.episode_id) == binding


def test_runtime_binding_rejects_stale_episode_owner(store) -> None:
    episode, lease, _ = make_running_system_episode(store)

    with pytest.raises(StaleWriterRejected):
        store.bind_episode_runtime(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token + 1,
            agent_session_id="agent-session-1",
            runtime="claude_code",
            native_session_id=None,
            runtime_generation="cc-process-1",
            runtime_pid=456,
            runtime_pgid=456,
            correlation_id="command.start.2",
            activate=False,
        )
