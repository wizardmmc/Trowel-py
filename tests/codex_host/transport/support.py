from __future__ import annotations

from trowel_py.codex_host import AppServerClient
from trowel_py.codex_host.version import CodexVersion
from tests.codex_host._fake import Behavior, FakeAppServer, Step


async def _version_0144() -> CodexVersion:
    return CodexVersion("codex-cli 0.144.0", (0, 144, 0))


def _build(
    behavior: Behavior,
    *,
    expected_version: str | None = "0.144.0",
    version_reader=_version_0144,
    **kwargs,
) -> tuple[AppServerClient, FakeAppServer]:
    fake = FakeAppServer(behavior)
    client = AppServerClient(
        codex_bin="codex",
        expected_version=expected_version,
        version_reader=version_reader,
        spawner=fake.spawner(),
        **kwargs,
    )
    return client, fake


def _initialize_response(request_id: object) -> Step:
    return Step.send(
        {
            "id": request_id,
            "result": {
                "userAgent": "Codex Desktop/0.144.0 (<platform>) dumb (trowel_codex_host)",
                "codexHome": "<codex-home>",
                "platformFamily": "unix",
                "platformOs": "macos",
            },
        }
    )


def _handshake() -> Behavior:
    async def behavior():
        msg = yield Step.recv()
        assert msg is not None and msg["method"] == "initialize"
        yield _initialize_response(msg["id"])
        msg = yield Step.recv()
        assert msg is not None and msg["method"] == "initialized"
        yield Step.recv()

    return behavior()
