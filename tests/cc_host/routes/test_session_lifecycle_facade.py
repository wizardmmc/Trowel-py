from __future__ import annotations

import inspect
import pickle
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from fastapi.routing import APIRoute

from trowel_py.cc_host import routes, session_lifecycle
from trowel_py.schemas.cc_host import CreateSessionRequest
from tests.cc_host.routes.support import _mini_app


def test_opened_session_type_keeps_its_route_module_identity() -> None:
    assert routes.OpenedCcSession.__module__ == "trowel_py.cc_host.routes"
    assert routes.OpenedCcSession.__qualname__ == "OpenedCcSession"
    assert inspect.getsourcefile(routes.OpenedCcSession) == routes.__file__
    assert pickle.loads(pickle.dumps(routes.OpenedCcSession)) is routes.OpenedCcSession


def test_route_facades_keep_public_identity_and_signatures() -> None:
    assert routes.get_registry.__module__ == "trowel_py.cc_host.routes"
    assert routes.open_cc_session.__module__ == "trowel_py.cc_host.routes"
    assert routes.close_cc_session.__module__ == "trowel_py.cc_host.routes"
    assert routes.open_cc_session.__qualname__ == "open_cc_session"
    assert routes.close_cc_session.__qualname__ == "close_cc_session"
    assert str(inspect.signature(routes.get_registry)) == ("() -> 'dict[str, CCHost]'")
    assert str(inspect.signature(routes.open_cc_session)) == (
        "(req: 'CreateSessionRequest', request: 'Request', "
        "registry: 'dict[str, CCHost] | None' = None) -> 'OpenedCcSession'"
    )
    assert str(inspect.signature(routes.close_cc_session)) == (
        "(session_id: 'str', registry: 'dict[str, CCHost] | None' = None) -> 'bool'"
    )


def test_open_facade_reads_current_route_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry: dict[str, Any] = {}
    workdir_index: dict[str, set[str]] = {}
    session_names: dict[str, str] = {}
    host_factory = object()
    observed: dict[str, Any] = {}
    host = cast(Any, object())

    def fake_open(
        req: CreateSessionRequest,
        target_registry: dict[str, Any],
        **dependencies: Any,
    ) -> tuple[str, Any, str]:
        observed.update(
            req=req,
            registry=target_registry,
            **dependencies,
        )
        return "new-session", host, "project"

    monkeypatch.setattr(routes, "_REGISTRY", registry)
    monkeypatch.setattr(routes, "_WORKDIR_INDEX", workdir_index)
    monkeypatch.setattr(routes, "_SESSION_NAMES", session_names)
    monkeypatch.setattr(routes, "_ACTIVE_SID", "old-session")
    monkeypatch.setattr(routes, "MAX_CONNECTIONS", 7)
    monkeypatch.setattr(routes, "CCHost", host_factory)
    monkeypatch.setattr(session_lifecycle, "open_session", fake_open)
    request = cast(
        Request,
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    proxy_base_url="http://127.0.0.1:8123",
                    cc_settings_path=tmp_path / "settings.json",
                )
            )
        ),
    )
    req = CreateSessionRequest(workdir=str(tmp_path), memory_enabled=False)

    result = routes.open_cc_session(req, request)

    assert result == routes.OpenedCcSession(
        sid="new-session",
        host=host,
        name="project",
    )
    assert observed == {
        "req": req,
        "registry": registry,
        "proxy_base_url": "http://127.0.0.1:8123",
        "settings_path": tmp_path / "settings.json",
        "workdir_index": workdir_index,
        "session_names": session_names,
        "max_connections": 7,
        "max_delegate_connections": routes.MAX_DELEGATE_CONNECTIONS,
        "host_factory": host_factory,
    }
    assert routes.get_active_session_id() == "new-session"


def test_open_cleans_owned_mcp_config_when_host_construction_fails(
    tmp_path: Path,
) -> None:
    config_path: Path | None = None

    def failing_factory(*args: Any, **kwargs: Any) -> None:
        nonlocal config_path
        config_path = Path(kwargs["mcp_config"])
        assert config_path.is_file()
        raise RuntimeError("host construction failed")

    with pytest.raises(RuntimeError, match="host construction failed"):
        session_lifecycle.open_session(
            CreateSessionRequest(workdir=str(tmp_path)),
            {},
            proxy_base_url=None,
            settings_path=None,
            workdir_index={},
            session_names={},
            max_connections=1,
            max_delegate_connections=1,
            host_factory=failing_factory,
        )

    assert config_path is not None
    assert not config_path.exists()


def test_discard_unstarted_session_removes_registry_and_owned_mcp_config(
    tmp_path: Path,
) -> None:
    registry: dict[str, routes.CCHost] = {}
    workdir_index: dict[str, set[str]] = {}
    session_names: dict[str, str] = {}
    sid, host, _ = session_lifecycle.open_session(
        CreateSessionRequest(workdir=str(tmp_path)),
        registry,
        proxy_base_url=None,
        settings_path=None,
        workdir_index=workdir_index,
        session_names=session_names,
        max_connections=1,
        max_delegate_connections=1,
        host_factory=routes.CCHost,
    )
    assert isinstance(host._mcp_config, str)
    config_path = Path(host._mcp_config)
    assert config_path.is_file()

    assert (
        session_lifecycle.discard_unstarted_session(
            sid,
            registry,
            workdir_index=workdir_index,
            session_names=session_names,
        )
        is True
    )

    assert registry == {}
    assert workdir_index == {}
    assert session_names == {}
    assert not config_path.exists()


def test_low_level_cc_capacity_counts_user_and_delegate_separately(
    tmp_path: Path,
) -> None:
    registry = {
        "delegate-1": SimpleNamespace(session_kind="delegate"),
    }

    def host_factory(*args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            session_kind=kwargs["session_kind"],
            workdir=str(tmp_path),
        )

    sid, _, _ = session_lifecycle.open_session(
        CreateSessionRequest(workdir=str(tmp_path), session_kind="user"),
        registry,
        proxy_base_url=None,
        settings_path=None,
        workdir_index={},
        session_names={},
        max_connections=1,
        max_delegate_connections=1,
        host_factory=host_factory,
    )

    assert sid in registry
    assert registry[sid].session_kind == "user"
    with pytest.raises(
        session_lifecycle.CcCapacityError,
        match="当前委派数量已满：连接上限为 1",
    ):
        session_lifecycle.open_session(
            CreateSessionRequest(workdir=str(tmp_path), session_kind="delegate"),
            registry,
            proxy_base_url=None,
            settings_path=None,
            workdir_index={},
            session_names={},
            max_connections=1,
            max_delegate_connections=1,
            host_factory=host_factory,
        )


def test_low_level_cc_capacity_reads_real_host_session_kind(
    tmp_path: Path,
) -> None:
    registry = {
        "delegate-1": routes.CCHost(
            "delegate-1",
            tmp_path,
            session_kind="delegate",
        )
    }

    sid, _, _ = session_lifecycle.open_session(
        CreateSessionRequest(workdir=str(tmp_path), session_kind="user"),
        registry,
        proxy_base_url=None,
        settings_path=None,
        workdir_index={},
        session_names={},
        max_connections=1,
        max_delegate_connections=1,
        host_factory=lambda *args, **kwargs: SimpleNamespace(
            session_kind=kwargs["session_kind"],
            workdir=str(tmp_path),
        ),
    )

    assert sid in registry


async def test_close_facade_reads_current_route_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry: dict[str, Any] = {"old-session": object()}
    workdir_index = {"/project": {"old-session"}}
    session_names = {"old-session": "project"}
    observed: dict[str, Any] = {}

    async def fake_close(
        session_id: str,
        target_registry: dict[str, Any],
        **dependencies: Any,
    ) -> bool:
        observed.update(
            session_id=session_id,
            registry=target_registry,
            **dependencies,
        )
        return True

    monkeypatch.setattr(routes, "_REGISTRY", registry)
    monkeypatch.setattr(routes, "_WORKDIR_INDEX", workdir_index)
    monkeypatch.setattr(routes, "_SESSION_NAMES", session_names)
    monkeypatch.setattr(routes, "_ACTIVE_SID", "old-session")
    monkeypatch.setattr(session_lifecycle, "close_session", fake_close)

    assert await routes.close_cc_session("old-session") is True
    assert observed == {
        "session_id": "old-session",
        "registry": registry,
        "workdir_index": workdir_index,
        "session_names": session_names,
    }
    assert routes.get_active_session_id() is None


async def test_close_failure_preserves_all_route_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingHost:
        workdir = "/project"

        async def close(self) -> None:
            raise RuntimeError("close failed")

    host = FailingHost()
    registry = {"old-session": cast(Any, host)}
    workdir_index = {"/project": {"old-session"}}
    session_names = {"old-session": "project"}
    monkeypatch.setattr(routes, "_REGISTRY", registry)
    monkeypatch.setattr(routes, "_WORKDIR_INDEX", workdir_index)
    monkeypatch.setattr(routes, "_SESSION_NAMES", session_names)
    routes.set_active_session_id("old-session")

    with pytest.raises(RuntimeError, match="close failed"):
        await routes.close_cc_session("old-session")

    assert registry == {"old-session": host}
    assert workdir_index == {"/project": {"old-session"}}
    assert session_names == {"old-session": "project"}
    assert routes.get_active_session_id() == "old-session"


async def test_direct_close_unknown_is_idempotent_but_delete_is_404() -> None:
    registry: dict[str, Any] = {}

    assert await routes.close_cc_session("missing", registry) is False
    assert _mini_app(registry).delete("/api/cc/sessions/missing").status_code == 404


def test_roster_falls_back_when_active_belongs_to_another_workdir() -> None:
    registry = {
        "other-active": SimpleNamespace(_init_roster=["wrong"]),
        "target-session": SimpleNamespace(_init_roster=["target"]),
    }

    roster = session_lifecycle.init_roster_for_workdir(
        "/target",
        cast(dict[str, Any], registry),
        {"/target": {"target-session"}, "/other": {"other-active"}},
        "other-active",
    )

    assert roster == ["target"]


def test_registry_dependencies_keep_the_route_facade_identity() -> None:
    dependencies = [
        dependency.call
        for route in routes.router.routes
        if isinstance(route, APIRoute)
        for dependency in route.dependant.dependencies
    ]

    assert dependencies.count(routes.get_registry) == 10
    assert all(dependency is routes.get_registry for dependency in dependencies)
