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


def test_pending_cc_request_exposes_question_without_changing_host(
    hub: SessionHub,
    workdir: Path,
    cc_registry: dict[str, FakeCcHost],
) -> None:
    binding = hub.create(cc_req(workdir))
    host = cc_registry[binding.session_id]
    host.pending_elicit = {
        "request_id": "request-cc-1",
        "questions": [
            {
                "question": "是否继续实现？",
                "header": "下一步",
                "options": [],
                "multiSelect": False,
            }
        ],
    }

    pending = hub.pending_request(binding.session_id, "request-cc-1")

    assert pending == {
        "kind": "input",
        "request_id": "request-cc-1",
        "prompt": "是否继续实现？",
        "questions": host.pending_elicit["questions"],
        "available_decisions": [],
    }
    assert host.elicit_answers == []
