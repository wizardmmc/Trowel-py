"""Tests for conversation extraction service + route (slice 018a).

Service mocks the LLM (no real calls); route uses TestClient with the LLM
dependency overridden. Fewer tests than the parser — higher on the pyramid.
"""
import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.cards.jsonl_parser import ChatMessage
from trowel_py.cards.routes import _get_llm_service
from trowel_py.cards.service import extract_from_conversation
from trowel_py.schemas.extracted_card import ExtractOutput, ExtractedCard


def _fake_llm_returning_one_card() -> MagicMock:
    """An LLMService-shaped mock that returns one fixed card via structured_call."""
    llm = MagicMock()
    llm.structured_call.return_value = ExtractOutput(cards=[
        ExtractedCard(
            title="useEffect cleanup",
            category="React",
            explanation="cleanup function runs before next effect or on unmount",
            tags=["hooks"],
            source_type="chat",
            confidence=4,
            difficulty=3,
        )
    ])
    return llm


# --- service: flattening logic ---

def test_extract_from_conversation_flattens_with_role_prefix():
    """Messages are joined as 'role: content' and passed to extract_cards."""
    llm = _fake_llm_returning_one_card()
    messages = [
        ChatMessage(role="user", content="what is useEffect?"),
        ChatMessage(role="assistant", content="it handles side effects"),
    ]
    extract_from_conversation(messages, llm)

    # the joined text is the first arg passed to structured_call
    called_prompt = llm.structured_call.call_args.args[0]
    assert "user: what is useEffect?" in called_prompt
    assert "assistant: it handles side effects" in called_prompt


def test_extract_from_conversation_returns_drafts():
    """Drafts come back from the (mocked) extraction pipeline."""
    llm = _fake_llm_returning_one_card()
    messages = [ChatMessage(role="user", content="q")]
    drafts = extract_from_conversation(messages, llm)
    assert len(drafts) == 1
    assert drafts[0].title == "useEffect cleanup"


# --- route: end-to-end with LLM mocked ---

@pytest.fixture
def client_with_fake_llm():
    """TestClient whose /extract-conversation uses a fake LLM, not env config."""
    app = create_app()
    app.dependency_overrides[_get_llm_service] = _fake_llm_returning_one_card
    return TestClient(app)


def test_extract_conversation_route_happy_path(client_with_fake_llm):
    """Valid JSONL -> 200 + drafts in the success envelope."""
    body_content = "\n".join([
        json.dumps({"role": "user", "content": "what is useEffect?"}),
        json.dumps({"role": "assistant", "content": "it handles side effects"}),
    ])
    resp = client_with_fake_llm.post(
        "/api/cards/extract-conversation",
        json={"content": body_content},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]["drafts"]) == 1
    assert body["data"]["drafts"][0]["title"] == "useEffect cleanup"


def test_extract_conversation_route_empty_input_rejected_at_schema(client_with_fake_llm):
    """Empty content is rejected at the schema layer (422), before the handler.

    ExtractRequest.content has min_length=1, so FastAPI returns 422 and the
    request never reaches parse_jsonl. This is the intended first line of
    defense; parse_jsonl's own empty-check is a safety net for direct calls.
    """
    resp = client_with_fake_llm.post(
        "/api/cards/extract-conversation",
        json={"content": ""},
    )
    assert resp.status_code == 422
    # FastAPI schema-error envelope shape: {"detail": [...]}
    assert "detail" in resp.json()
