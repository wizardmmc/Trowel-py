from __future__ import annotations

import pytest

from trowel_py.model_os.types import EpisodeStatus


# active 可协作式进入 checkpointing；close_episode 只接受 checkpointing，reconcile/recovery 有独立终态路径。
LEGAL_TRANSITIONS: dict[EpisodeStatus, frozenset[EpisodeStatus]] = {
    EpisodeStatus.STARTING: frozenset({EpisodeStatus.ACTIVE, EpisodeStatus.FAILED}),
    EpisodeStatus.ACTIVE: frozenset(
        {
            EpisodeStatus.YIELD_REQUESTED,
            EpisodeStatus.SUSPENDED_WAITING_INPUT,
            EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
            EpisodeStatus.CHECKPOINTING,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.YIELD_REQUESTED: frozenset(
        {EpisodeStatus.CHECKPOINTING, EpisodeStatus.FAILED, EpisodeStatus.RECOVERING}
    ),
    EpisodeStatus.CHECKPOINTING: frozenset(
        {EpisodeStatus.CLOSED, EpisodeStatus.FAILED, EpisodeStatus.RECOVERING}
    ),
    EpisodeStatus.SUSPENDED_WAITING_INPUT: frozenset(
        {
            EpisodeStatus.SUSPENDED_READY,
            EpisodeStatus.RECONCILE_REQUIRED,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.SUSPENDED_WAITING_APPROVAL: frozenset(
        {
            EpisodeStatus.SUSPENDED_READY,
            EpisodeStatus.RECONCILE_REQUIRED,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.SUSPENDED_READY: frozenset(
        {
            EpisodeStatus.ACTIVE,
            EpisodeStatus.RECONCILE_REQUIRED,
            EpisodeStatus.FAILED,
            EpisodeStatus.RECOVERING,
        }
    ),
    EpisodeStatus.RECONCILE_REQUIRED: frozenset(
        {
            EpisodeStatus.CLOSED,
            EpisodeStatus.CHECKPOINTING,
            EpisodeStatus.SUSPENDED_READY,
            EpisodeStatus.ACTIVE,
            EpisodeStatus.FAILED,
        }
    ),
    EpisodeStatus.RECOVERING: frozenset(
        {EpisodeStatus.ACTIVE, EpisodeStatus.CLOSED, EpisodeStatus.FAILED}
    ),
    EpisodeStatus.CLOSED: frozenset(),
    EpisodeStatus.FAILED: frozenset(),
}


@pytest.mark.parametrize(
    "src,target,legal",
    [
        (EpisodeStatus.ACTIVE, EpisodeStatus.YIELD_REQUESTED, True),
        (EpisodeStatus.ACTIVE, EpisodeStatus.SUSPENDED_WAITING_INPUT, True),
        (EpisodeStatus.YIELD_REQUESTED, EpisodeStatus.CHECKPOINTING, True),
        (EpisodeStatus.CHECKPOINTING, EpisodeStatus.CLOSED, True),
        (EpisodeStatus.SUSPENDED_WAITING_INPUT, EpisodeStatus.SUSPENDED_READY, True),
        (EpisodeStatus.SUSPENDED_READY, EpisodeStatus.ACTIVE, True),
        (EpisodeStatus.ACTIVE, EpisodeStatus.CLOSED, False),
        (EpisodeStatus.ACTIVE, EpisodeStatus.SUSPENDED_READY, False),
        (EpisodeStatus.RECONCILE_REQUIRED, EpisodeStatus.CLOSED, True),
        (EpisodeStatus.SUSPENDED_WAITING_INPUT, EpisodeStatus.ACTIVE, False),
    ],
)
def test_state_machine_transition_legality(src, target, legal) -> None:
    edge_legal = target in LEGAL_TRANSITIONS.get(src, frozenset())
    assert edge_legal is legal, (
        f"expected src={src.value} → target={target.value} legal={legal}, "
        f"but the frozen graph says legal={edge_legal}"
    )


def test_terminal_states_have_no_outgoing_edge() -> None:
    assert LEGAL_TRANSITIONS[EpisodeStatus.CLOSED] == frozenset()
    assert LEGAL_TRANSITIONS[EpisodeStatus.FAILED] == frozenset()
