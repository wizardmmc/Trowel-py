import pytest

from trowel_py.agent_host.runtimes.codex import CodexEventAdapter


@pytest.fixture()
def adapter() -> CodexEventAdapter:
    return CodexEventAdapter(session_id="codex-sess")
