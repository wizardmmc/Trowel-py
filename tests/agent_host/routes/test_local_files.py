from pathlib import Path

from fastapi.testclient import TestClient

from tests.agent_host.routes.support import cc_payload, create_session


def _file_url(session_id: str, path: str) -> str:
    return f"/api/agent/sessions/{session_id}/files?path={path}"


def test_session_file_serves_project_html_in_a_sandbox(
    client: TestClient,
    workdir: Path,
) -> None:
    html = workdir / "docs" / "example.html"
    html.parent.mkdir()
    html.write_text("<h1>Local prototype</h1>", encoding="utf-8")
    session = create_session(client, cc_payload(workdir))

    response = client.get(_file_url(session["session_id"], "docs/example.html"))

    assert response.status_code == 200
    assert response.text == "<h1>Local prototype</h1>"
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"].startswith(
        "sandbox allow-scripts;"
    )
    assert "allow-same-origin" not in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_session_file_rejects_parent_traversal(
    client: TestClient,
    workdir: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.html"
    outside.write_text("secret", encoding="utf-8")
    session = create_session(client, cc_payload(workdir))

    response = client.get(_file_url(session["session_id"], "../outside.html"))

    assert response.status_code == 403
    assert "secret" not in response.text


def test_session_file_rejects_symlink_escape(
    client: TestClient,
    workdir: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.html"
    outside.write_text("secret", encoding="utf-8")
    (workdir / "linked.html").symlink_to(outside)
    session = create_session(client, cc_payload(workdir))

    response = client.get(_file_url(session["session_id"], "linked.html"))

    assert response.status_code == 403
    assert "secret" not in response.text


def test_session_file_rejects_intermediate_symlink_escape(
    client: TestClient,
    workdir: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.html").write_text("secret", encoding="utf-8")
    (workdir / "linked").symlink_to(outside, target_is_directory=True)
    session = create_session(client, cc_payload(workdir))

    response = client.get(_file_url(session["session_id"], "linked/secret.html"))

    assert response.status_code == 403
    assert "secret" not in response.text


def test_session_file_rejects_absolute_and_missing_paths(
    client: TestClient,
    workdir: Path,
) -> None:
    session = create_session(client, cc_payload(workdir))
    session_id = session["session_id"]

    absolute = client.get(_file_url(session_id, "/tmp/outside.html"))
    missing = client.get(_file_url(session_id, "missing.html"))

    assert absolute.status_code == 400
    assert missing.status_code == 404


def test_session_file_requires_a_known_session(
    client: TestClient,
) -> None:
    response = client.get(_file_url("unknown", "docs/example.html"))

    assert response.status_code == 404
