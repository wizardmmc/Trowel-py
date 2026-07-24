import ast
from pathlib import Path

import pytest

from trowel_py.agent_host import hub as hub_module
from trowel_py.agent_host.hub import (
    InvalidSessionRequestError,
    RuntimeTurnError,
    RuntimeUnavailableError,
    SessionAccessError,
    SessionConflictError,
    SessionHubError,
    SessionNotFoundError,
    SessionOperationError,
)
from trowel_py.agent_host.routes import _http_exception


def test_session_hub_has_no_http_framework_import_or_request_annotation() -> None:
    source = Path(hub_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )

    assert imported_roots.isdisjoint({"fastapi", "starlette"})
    assert "Request" not in {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        (InvalidSessionRequestError, 400),
        (SessionAccessError, 403),
        (SessionNotFoundError, 404),
        (SessionConflictError, 409),
        (SessionOperationError, 422),
        (RuntimeTurnError, 502),
        (RuntimeUnavailableError, 503),
    ],
)
def test_session_hub_errors_map_to_http_at_route_boundary(
    error_type: type[SessionHubError],
    status_code: int,
) -> None:
    mapped = _http_exception(error_type("stable detail"))

    assert mapped.status_code == status_code
    assert mapped.detail == "stable detail"
