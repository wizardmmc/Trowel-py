from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.cc_host.service import CCHost
from trowel_py.schemas.cc_host import ElicitationRequestEvent

from tests.cc_host.service._support import (
    FakeProc,
    FakeSpawner,
    collect,
    init_event,
    line,
    result_ok,
)


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


class TestPlanModeControl:
    @pytest.mark.parametrize("tool_name", ["EnterPlanMode", "ExitPlanMode"])
    async def test_plan_mode_request_waits_for_frontend_approval(
        self,
        tmp_path: Path,
        tool_name: str,
    ):
        # CC 2.1.197 + isolated GLM 的 ExitPlanMode 实录使用空 input 的
        # can_use_tool；上游源码确认 EnterPlanMode 走同一交互权限通道。
        proc = FakeProc(
            [
                line(init_event()),
                line(
                    {
                        "type": "control_request",
                        "request_id": "req-plan-mode",
                        "request": {
                            "subtype": "can_use_tool",
                            "tool_name": tool_name,
                            "input": {},
                            "tool_use_id": "call_plan_mode",
                        },
                    }
                ),
                line(result_ok()),
            ]
        )
        host = CCHost("sid", tmp_path, spawner=FakeSpawner([proc]))

        events = await collect(host.send("切换 plan mode"))

        assert any(isinstance(event, ElicitationRequestEvent) for event in events)
        assert host._pending_elicit is not None
        assert host._pending_elicit["tool_name"] == tool_name
        assert not [
            json.loads(item.decode())
            for item in proc.stdin.written
            if json.loads(item.decode()).get("type") == "control_response"
        ]

        assert await host.answer_elicit({"计划模式": "允许"}) is True
        responses = [
            json.loads(item.decode())
            for item in proc.stdin.written
            if json.loads(item.decode()).get("type") == "control_response"
        ]
        assert responses == [
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": "req-plan-mode",
                    "response": {
                        "behavior": "allow",
                        "updatedInput": {},
                    },
                },
            }
        ]
