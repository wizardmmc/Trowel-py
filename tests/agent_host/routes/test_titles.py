from __future__ import annotations

from tests.agent_host.routes.support import cc_payload, create_session


def test_manual_rename_updates_active_session(client, workdir):
    created = create_session(client, cc_payload(workdir))

    response = client.put(
        f"/api/agent/sessions/{created['session_id']}/title",
        json={"title": "  手动标题  "},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["display_title"] == "手动标题"
    assert data["title_source"] == "manual"
    active = client.get("/api/agent/sessions/active").json()["data"]["sessions"]
    assert active[0]["display_title"] == "手动标题"


def test_generate_title_falls_back_to_prompt_without_generator(client, workdir):
    created = create_session(client, cc_payload(workdir))

    response = client.post(
        f"/api/agent/sessions/{created['session_id']}/title/generate",
        json={"text": "  研究   同目录\n会话聚合  "},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["display_title"] == "研究 同目录 会话聚合"
    assert data["title_source"] == "prompt"


def test_blank_manual_title_is_rejected(client, workdir):
    created = create_session(client, cc_payload(workdir))

    response = client.put(
        f"/api/agent/sessions/{created['session_id']}/title",
        json={"title": "   "},
    )

    assert response.status_code == 422


def test_multiline_manual_title_is_rejected(client, workdir):
    created = create_session(client, cc_payload(workdir))

    response = client.put(
        f"/api/agent/sessions/{created['session_id']}/title",
        json={"title": "第一行\n第二行"},
    )

    assert response.status_code == 422
