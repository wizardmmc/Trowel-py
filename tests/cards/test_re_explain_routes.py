from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app
from trowel_py.cards.routes import _get_llm_service
from trowel_py.schemas.re_explain import ReExplainResultSchema


def _fake_re_explain_llm() -> MagicMock:
    llm = MagicMock()
    llm.structured_call.return_value = ReExplainResultSchema(
        explanation="a fresh explanation phrased from a different angle"
    )
    return llm


@pytest.fixture
def re_explain_app():
    app = create_app()
    app.dependency_overrides[_get_llm_service] = _fake_re_explain_llm
    yield app
    app.dependency_overrides.clear()


def test_re_explain_returns_new_explanation(re_explain_app):
    client = TestClient(re_explain_app)
    resp = client.post(
        "/api/cards/re-explain",
        json={
            "explanation": "the original explanation that the user wants improved",
            "title": "useEffect",
            "category": "React",
            "user_hint": "更通俗一点",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["error"] is None
    assert (
        body["data"]["explanation"]
        == "a fresh explanation phrased from a different angle"
    )


def test_re_explain_422_when_explanation_too_short(re_explain_app):
    client = TestClient(re_explain_app)
    resp = client.post(
        "/api/cards/re-explain",
        json={
            "explanation": "short",
            "title": "useEffect",
            "category": "React",
        },
    )
    assert resp.status_code == 422


def test_re_explain_works_without_hint(re_explain_app):
    client = TestClient(re_explain_app)
    resp = client.post(
        "/api/cards/re-explain",
        json={
            "explanation": "the original explanation that the user wants improved",
            "title": "useEffect",
            "category": "React",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["explanation"]


def test_re_explain_422_when_title_missing(re_explain_app):
    client = TestClient(re_explain_app)
    resp = client.post(
        "/api/cards/re-explain",
        json={
            "explanation": "the original explanation that the user wants improved",
            "category": "React",
        },
    )
    assert resp.status_code == 422
