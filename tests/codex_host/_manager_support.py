from __future__ import annotations

import json
from pathlib import Path

from trowel_py.codex_host import (
    AppServerClient,
    CodexEvent,
    CodexEventType,
    CodexHostManager,
    CodexSessionConfig,
)
from trowel_py.codex_host.version import CodexVersion
from tests.codex_host._fake import FakeAppServer, Step


async def _version_0144() -> CodexVersion:
    """返回已验证版本，避免测试启动真实 ``codex``。"""

    return CodexVersion("codex-cli 0.144.0", (0, 144, 0))


def _init_resp(request_id: object) -> Step:
    return Step.send(
        {
            "id": request_id,
            "result": {
                "userAgent": "Codex/0.144.0 (trowel)",
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
            },
        }
    )


def _thread_result(tid: str) -> dict:
    return {
        "thread": {"id": tid},
        "model": "gpt-5.6-sol",
        "modelProvider": "openai",
        "cwd": "/tmp/x",
        "sandbox": {"mode": "read-only"},
        "approvalPolicy": {"policy": "never"},
        "serviceTier": None,
        "reasoningEffort": "high",
    }


def _cfg(sid: str) -> CodexSessionConfig:
    return CodexSessionConfig(trowel_session_id=sid, workdir="/tmp/x")


def _manager(
    fake: FakeAppServer, *, pending_request_timeout_s: float = 600.0
) -> CodexHostManager:
    """构造复用同一 fake client 的 manager；重启场景使用 `_restart_manager`。"""

    holder: dict[str, AppServerClient] = {}

    def factory() -> AppServerClient:
        if "client" not in holder:
            holder["client"] = AppServerClient(
                codex_bin="codex",
                expected_version="0.144.0",
                version_reader=_version_0144,
                spawner=fake.spawner(),
                close_grace_s=0.2,
                close_term_s=0.2,
            )
        return holder["client"]

    return CodexHostManager(
        client_factory=factory,
        pending_request_timeout_s=pending_request_timeout_s,
    )


def _model_list_fixture() -> dict:
    """读取来自 Codex 0.144.0 的脱敏 ``model/list`` 真实录制。"""

    path = Path(__file__).parent / "fixtures" / "model-list-0.144.0.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _server_request_fixture(name: str) -> dict:
    """读取一份脱敏的真实 server request 录制。"""

    path = Path(__file__).parent / "fixtures" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _restart_manager(fakes: list[FakeAppServer]) -> CodexHostManager:
    """构造每次连接生命周期都换新 fake 的 manager。"""

    state = {"i": 0}

    def factory() -> AppServerClient:
        fake = fakes[state["i"]]
        state["i"] += 1
        return AppServerClient(
            codex_bin="codex",
            expected_version="0.144.0",
            version_reader=_version_0144,
            spawner=fake.spawner(),
            close_grace_s=0.2,
            close_term_s=0.2,
        )

    return CodexHostManager(client_factory=factory)


def _behavior_server(
    *,
    on_turn=None,
    exit_after_turn: bool = False,
    block_on_turn_start: bool = False,
    orphan_before: list[dict] | None = None,
):
    """构造通用 app-server 脚本，支持 turn 注入、异常退出、阻塞和孤儿通知。"""

    async def behavior():
        counter = 0
        msg = yield Step.recv()
        yield _init_resp(msg["id"])
        yield Step.recv()
        for note in orphan_before or ():
            yield Step.send(note)
        while True:
            msg = yield Step.recv()
            if msg is None:
                return
            method = msg["method"]
            request_id = msg["id"]
            if method == "thread/start":
                counter += 1
                yield Step.send(
                    {"id": request_id, "result": _thread_result(f"t-{counter}")}
                )
            elif method == "thread/resume":
                thread_id = msg["params"]["threadId"]
                yield Step.send(
                    {
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": f"no rollout found for thread id {thread_id}",
                        },
                    }
                )
            elif method == "turn/start":
                params = msg["params"]
                thread_id = params["threadId"]
                counter += 1
                turn_id = f"turn-{counter}"
                yield Step.send({"id": request_id, "result": {"turn": {"id": turn_id}}})
                if block_on_turn_start:
                    await (yield Step.hold(30))
                # 真实 app-server 在响应和通知之间存在推理间隔，fake 必须让出调度。
                yield Step.hold(0.01)
                if on_turn is not None:
                    for step in on_turn(thread_id, turn_id):
                        yield step
                yield Step.send(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": thread_id,
                            "turn": {
                                "id": turn_id,
                                "status": "completed",
                                "durationMs": 5,
                            },
                        },
                    }
                )
                if exit_after_turn:
                    yield Step.exit(0)
            elif method == "turn/interrupt":
                params = msg["params"]
                yield Step.send({"id": request_id, "result": {}})
                yield Step.send(
                    {
                        "method": "turn/completed",
                        "params": {
                            "threadId": params["threadId"],
                            "turn": {
                                "id": params["turnId"],
                                "status": "interrupted",
                            },
                        },
                    }
                )
            else:
                yield Step.send({"id": request_id, "result": {}})

    return behavior()


def _deltas(thread_id: str, turn_id: str) -> list[Step]:
    return [
        Step.send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "itemId": "m1",
                    "delta": f"hi-{thread_id}",
                },
            }
        ),
        Step.send(
            {
                "method": "item/completed",
                "params": {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "type": "agentMessage",
                        "id": "m1",
                        "text": f"hi-{thread_id}",
                        "phase": "final_answer",
                    },
                },
            }
        ),
    ]


def _has(events: list[CodexEvent], type_: CodexEventType) -> bool:
    return any(e.type is type_ for e in events)
