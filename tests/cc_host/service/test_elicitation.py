from __future__ import annotations

import json
from pathlib import Path

from trowel_py.cc_host.service import CCHost

from tests.cc_host.service._support import FakeProc, FakeSpawner


# AskUserQuestion 的真实 control_response 要求 updatedInput.answers，即使答案为空也不能省略。
class TestElicitAnswer:
    async def test_answer_elicit_writes_allow_with_answers(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        host._proc = proc
        host._pending_elicit = {
            "request_id": "req-1",
            "tool_use_id": "call_abc",
            "questions": [
                {
                    "question": "A or B?",
                    "header": "Pref",
                    "options": [{"label": "A"}],
                    "multiSelect": False,
                }
            ],
        }
        ok = await host.answer_elicit({"A or B?": "A"})
        assert ok is True
        assert host._pending_elicit is None
        payload = json.loads(proc.stdin.written[-1].decode())
        assert payload["type"] == "control_response"
        assert payload["response"]["subtype"] == "success"
        assert payload["response"]["request_id"] == "req-1"
        assert payload["response"]["response"]["behavior"] == "allow"
        updated = payload["response"]["response"]["updatedInput"]
        assert updated["answers"] == {"A or B?": "A"}
        assert updated["questions"][0]["header"] == "Pref"
        assert updated["annotations"] == {}

    async def test_cancel_elicit_writes_deny(self, tmp_path: Path):
        proc = FakeProc([])
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))
        host._proc = proc
        host._pending_elicit = {
            "request_id": "req-2",
            "tool_use_id": "c",
            "questions": [],
        }
        ok = await host.cancel_elicit()
        assert ok is True
        assert host._pending_elicit is None
        payload = json.loads(proc.stdin.written[-1].decode())
        assert payload["response"]["response"]["behavior"] == "deny"
        assert "declined" in payload["response"]["response"]["message"]

    async def test_answer_without_pending_returns_false(self, tmp_path: Path):
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([FakeProc([])]))
        ok = await host.answer_elicit({"q": "a"})
        assert ok is False
