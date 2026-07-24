"""验证 journal payload 的递归脱敏与 Store 持久化隐私边界。"""

from __future__ import annotations

from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def test_redacts_api_key_by_key_name() -> None:
    payload = {"api_key": "sk-live-1234567890"}

    first = redact_payload(payload)
    second = redact_payload(payload)

    assert first == {"api_key": "<redacted:sha256=b11c97b33cee:len=18>"}
    assert second == first
    assert payload == {"api_key": "sk-live-1234567890"}


def test_redacts_bearer_token_value() -> None:
    out = redact_payload({"header": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"})
    assert "Bearer" not in str(out["header"])
    assert out["header"] != "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"


def test_redacts_proxy_url() -> None:
    out = redact_payload({"https_proxy": "http://127.0.0.1:7897"})
    assert "127.0.0.1:7897" not in str(out["https_proxy"])


def test_redacts_prompt_and_thinking_and_private_chat() -> None:
    raw = {
        "prompt": "user's full private message about their health record",
        "thinking": "the model's private chain of thought",
        "private_chat": "secret conversation content",
        "content": "another piece of full user content",
    }
    out = redact_payload(raw)
    for key in ("prompt", "thinking", "private_chat", "content"):
        assert out[key] != raw[key], f"{key} was not redacted"
        assert raw[key] not in str(out[key]), f"{key} value leaked into the log"


def test_preserves_structural_fields() -> None:
    out = redact_payload(
        {
            "new_status": "running",
            "kind": "task",
            "count": 3,
            "ratio": 0.78,
            "model": "glm-5.2",
        }
    )
    assert out["new_status"] == "running"
    assert out["kind"] == "task"
    assert out["count"] == 3
    assert out["ratio"] == 0.78
    assert out["model"] == "glm-5.2"


def test_redacts_nested_secrets() -> None:
    out = redact_payload(
        {
            "envelope": {
                "auth": {"token": "sk-nested-abc"},
                "meta": {"prompt": "private nested prompt"},
            },
        }
    )
    assert out["envelope"]["auth"]["token"] != "sk-nested-abc"
    assert "private nested prompt" not in str(out["envelope"]["meta"]["prompt"])


def test_store_redacts_payload_before_persisting(store: ModelOsStore) -> None:
    ev = EventEnvelope(
        event_id="evt-secret",
        kind=EventKind.NOTE,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={
            "api_key": "sk-LEAK-1234567890abcdef",
            "prompt": "user said: my password is hunter2",
            "https_proxy": "http://127.0.0.1:7897",
            "model": "glm-5.2",
        },
    )
    store.append_event(ev)

    events = store.list_events()
    assert len(events) == 1
    stored_payload = events[0][1].payload
    assert stored_payload["model"] == "glm-5.2"
    assert stored_payload["api_key"] != "sk-LEAK-1234567890abcdef"
    assert "sk-LEAK" not in str(stored_payload["api_key"])
    assert "hunter2" not in str(stored_payload["prompt"])
    assert "127.0.0.1:7897" not in str(stored_payload["https_proxy"])
