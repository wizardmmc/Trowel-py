from __future__ import annotations

import inspect
import pickle

from trowel_py import codex_host
from trowel_py.codex_host import session, session_types


def test_session_types_keep_public_reexport_identity_and_signatures() -> None:
    names = (
        "TrowelMemoryMcpConfig",
        "CodexSessionConfig",
        "ThreadBinding",
        "build_default_trowel_memory_mcp",
        "parse_thread_binding",
    )

    for name in names:
        public = getattr(session, name)
        assert public is getattr(session_types, name)
        assert public.__module__ == session.__name__
        assert pickle.loads(pickle.dumps(public)) is public

    for name in ("CodexSessionConfig", "ThreadBinding", "parse_thread_binding"):
        assert getattr(session, name) is getattr(codex_host, name)

    assert str(inspect.signature(session.parse_thread_binding)) == (
        "(result: 'Mapping[str, Any]') -> 'ThreadBinding'"
    )


def test_attach_thread_binding_uses_session_facade_at_call_time(monkeypatch) -> None:
    expected = session.ThreadBinding(
        thread_id="thread-from-patch",
        model="model",
        model_provider="provider",
        cwd="/tmp/workspace",
        sandbox={},
        approval_policy=None,
    )
    seen: list[object] = []

    def parse(result):
        seen.append(result)
        return expected

    monkeypatch.setattr(session, "parse_thread_binding", parse)
    target = session.CodexSession(
        session.CodexSessionConfig("session-a", "/tmp/workspace")
    )
    payload = {"thread": {"id": "ignored"}}

    assert target.attach_thread_binding(payload) is expected
    assert target.binding is expected
    assert seen == [payload]
