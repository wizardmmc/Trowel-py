from pathlib import Path

import pytest

from trowel_py.agent_host.hub import SessionHub
from tests.agent_host.hub._support import (
    FakeCcHost,
    FakeCodexManager,
    cc_req,
    codex_req,
)


@pytest.mark.anyio
async def test_managed_codex_answer_uses_existing_request_path(
    hub: SessionHub,
    workdir: Path,
    codex_mgr: FakeCodexManager,
) -> None:
    binding = hub.create(codex_req(workdir))

    result = await hub.answer_managed_pending(
        binding.session_id,
        {"request_id": "request-1", "decision": "accept"},
    )

    assert result["status"] == "answered"
    assert codex_mgr.answered_requests == [(binding.session_id, "request-1", "accept")]


@pytest.mark.anyio
async def test_managed_cc_answer_and_cancel_use_existing_elicit_path(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    binding = hub.create(cc_req(workdir))
    host = cc_registry[binding.session_id]

    answered = await hub.answer_managed_pending(
        binding.session_id,
        {"cancel": False, "answers": {"匿名问题": "匿名回答"}},
    )
    cancelled = await hub.answer_managed_pending(
        binding.session_id,
        {"cancel": True, "answers": {}},
    )

    assert answered is True
    assert cancelled is True
    assert host.elicit_answers == [{"匿名问题": "匿名回答"}]
    assert host.elicit_cancelled == 1
