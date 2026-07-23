import pytest

from trowel_py.agent_host.codex_adapter import CodexEventAdapter


@pytest.fixture()
def adapter() -> CodexEventAdapter:
    return CodexEventAdapter(session_id="codex-sess")
