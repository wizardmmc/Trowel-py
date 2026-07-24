import json
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trowel_py.cc_host import routes as cc_routes
from trowel_py.cc_host.routes import get_registry, router as cc_router


class FakeHost:
    def __init__(
        self,
        events: list,
        cc_session_id: str = "s-1",
        workdir: str = "/wd",
    ) -> None:
        self._events = events
        self.cc_session_id = cc_session_id
        self.model = "glm-5.2"
        self.effort = "medium"
        self.workdir = workdir
        self.interrupted = False
        self.closed = False
        self.reloaded = False
        self.received: list[str] = []
        self.answered: dict[str, str] | None = None
        self.cancelled = False
        # is_dead 区分未启动的临时会话与活动会话。
        self.is_dead = False

    async def send(self, text: str) -> AsyncIterator:
        self.received.append(text)
        for e in self._events:
            yield e

    async def interrupt(self) -> None:
        self.interrupted = True

    async def answer_elicit(self, answers: dict[str, str]) -> bool:
        self.answered = dict(answers)
        return True

    async def cancel_elicit(self) -> bool:
        self.cancelled = True
        return True

    async def reload(self) -> None:
        self.reloaded = True

    async def close(self) -> None:
        self.closed = True


def _mini_app(registry: dict) -> TestClient:
    # 每个 app 都重置模块级会话状态，避免测试相互污染。
    cc_routes._WORKDIR_INDEX.clear()
    cc_routes._SESSION_NAMES.clear()
    cc_routes._ACTIVE_SID = None
    app = FastAPI()
    app.include_router(cc_router, prefix="/api/cc")
    app.dependency_overrides[get_registry] = lambda: registry
    return TestClient(app)


def _parse_sse(resp) -> list[dict]:
    out = []
    for raw in resp.iter_lines():
        if isinstance(raw, bytes):
            raw = raw.decode()
        if raw.startswith("data: "):
            out.append(json.loads(raw[len("data: ") :]))
    return out
