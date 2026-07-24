import json

from trowel_py.schemas.cc_host import (
    FinishedEvent,
    SessionStartedEvent,
    TextEvent,
)
from tests.cc_host.routes.support import FakeHost, _mini_app, _parse_sse


class TestMessagesSSE:
    def test_streams_trowel_events_as_sse(self):
        sid = "x"
        fake = FakeHost(
            [
                SessionStartedEvent(
                    type="session_started",
                    model="glm-5.2",
                    cwd="/wd",
                    cc_session_id="s-1",
                    tools=["Read"],
                ),
                TextEvent(type="text", text="hello"),
                FinishedEvent(
                    type="finished",
                    usage={},
                    total_cost_usd=0.0,
                    num_turns=1,
                ),
            ]
        )
        client = _mini_app({sid: fake})
        with client.stream(
            "POST",
            f"/api/cc/sessions/{sid}/messages",
            json={"text": "hi"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            events = _parse_sse(resp)
        kinds = [e["type"] for e in events]
        assert kinds == ["session_started", "text", "finished"]
        assert events[1]["text"] == "hello"

    def test_send_failure_after_stream_start_returns_one_terminal_error(self):
        class FailingHost(FakeHost):
            async def send(self, text: str):
                self.received.append(text)
                yield TextEvent(type="text", text="已进入流")
                raise RuntimeError("流内失败")

        client = _mini_app({"x": FailingHost([])})
        with client.stream(
            "POST",
            "/api/cc/sessions/x/messages",
            json={"text": "继续"},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")
            body = b"".join(resp.iter_bytes())

        expected = (
            'data: {"type":"text","text":"已进入流"}\n\n'
            'data: {"type":"error","subclass":"host_error",'
            '"errors":["流内失败"],"api_error_status":null}\n\n'
        ).encode()
        assert body == expected

        events = [
            json.loads(line.removeprefix("data: "))
            for line in body.decode().splitlines()
            if line
        ]
        terminal = [
            event
            for event in events
            if event["type"] in {"finished", "error", "interrupted"}
        ]
        assert terminal == [
            {
                "type": "error",
                "subclass": "host_error",
                "errors": ["流内失败"],
                "api_error_status": None,
            }
        ]

    def test_unknown_session_404(self):
        client = _mini_app({})
        resp = client.post("/api/cc/sessions/nope/messages", json={"text": "x"})
        assert resp.status_code == 404


class TestInterrupt:
    def test_interrupt_calls_host(self):
        sid = "x"
        fake = FakeHost([])
        client = _mini_app({sid: fake})
        resp = client.post(f"/api/cc/sessions/{sid}/interrupt")
        assert resp.status_code == 200
        assert resp.json()["data"]["interrupted"] is True
        assert fake.interrupted is True

    def test_interrupt_unknown_404(self):
        client = _mini_app({})
        assert client.post("/api/cc/sessions/nope/interrupt").status_code == 404


class TestAnswer:
    def test_answer_calls_host_with_answers(self):
        fake = FakeHost([])
        client = _mini_app({"s1": fake})
        resp = client.post(
            "/api/cc/sessions/s1/answer",
            json={"answers": {"A or B?": "A"}, "cancel": False},
        )
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["answered"] is True
        assert fake.answered == {"A or B?": "A"}

    def test_answer_cancel_calls_host(self):
        fake = FakeHost([])
        client = _mini_app({"s1": fake})
        resp = client.post(
            "/api/cc/sessions/s1/answer",
            json={"answers": {}, "cancel": True},
        )
        assert resp.json()["success"] is True
        assert fake.cancelled is True
        assert fake.answered is None

    def test_answer_unknown_404(self):
        client = _mini_app({})
        assert (
            client.post(
                "/api/cc/sessions/nope/answer",
                json={"answers": {}, "cancel": False},
            ).status_code
            == 404
        )


class TestDelete:
    def test_delete_closes_and_removes(self):
        sid = "x"
        fake = FakeHost([])
        reg = {sid: fake}
        client = _mini_app(reg)
        resp = client.delete(f"/api/cc/sessions/{sid}")
        assert resp.status_code == 200
        assert fake.closed is True
        assert sid not in reg
