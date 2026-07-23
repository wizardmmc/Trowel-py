from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    immutable_payload,
)


def make_codex_event(
    type_: CodexEventType,
    *,
    seq: int,
    thread_id: str | None = "thr-1",
    turn_id: str | None = "turn-1",
    item_id: str | None = None,
    payload: dict | None = None,
    session_id: str = "codex-sess",
) -> CodexEvent:
    return CodexEvent(
        session_id=session_id,
        seq=seq,
        type=type_,
        thread_id=thread_id,
        turn_id=turn_id,
        item_id=item_id,
        payload=immutable_payload(**(payload or {})),
    )
