"""验证旧 CC 兼容路由不能读取、恢复或操纵 discussion participant。"""

from __future__ import annotations

from trowel_py.cc_host import routes as cc_routes
from trowel_py.cc_host.session_scan import SessionSummary
from tests.cc_host.routes.support import FakeHost, _mini_app


class _FakeAgentHub:
    """只提供旧 CC 路由查询非用户原生身份所需的最小端口。"""

    def non_user_native_ids(self, runtime: object) -> frozenset[str]:
        """返回一个已由 discussion 持有的 CC 原生会话 ID。"""

        del runtime
        return frozenset({"private-native"})


def test_legacy_routes_hide_discussion_sid_and_reject_reserved_creation(
    tmp_path,
) -> None:
    """知道 participant sid 也不能读历史、发消息、删除或新建同类会话。"""

    host = FakeHost([], cc_session_id="private-native", workdir=str(tmp_path))
    host.session_kind = "discussion"
    client = _mini_app({"private-sid": host})
    client.app.state.agent_hub = _FakeAgentHub()

    assert client.get("/api/cc/sessions/private-sid/history").status_code == 404
    assert (
        client.post(
            "/api/cc/sessions/private-sid/messages",
            json={"text": "越权续写"},
        ).status_code
        == 404
    )
    assert client.delete("/api/cc/sessions/private-sid").status_code == 404
    assert host.received == []
    assert host.closed is False
    assert (
        client.post(
            "/api/cc/sessions",
            json={"workdir": str(tmp_path), "session_kind": "discussion"},
        ).status_code
        == 404
    )


def test_legacy_history_and_resume_exclude_non_user_native_identity(
    tmp_path,
    monkeypatch,
) -> None:
    """无需 participant sid 的历史下拉和 user resume 也不能越过长期黑名单。"""

    observed: dict[str, frozenset[str]] = {}

    def fake_list_sessions(
        workdir: str,
        *,
        limit: int | None = None,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[SessionSummary]:
        """记录路由传入的排除集合并模拟 scanner 已完成过滤。"""

        del workdir, limit
        observed["excluded"] = excluded_ids
        rows = [
            SessionSummary("private-native", "private", 2.0),
            SessionSummary("visible-native", "visible", 1.0),
        ]
        return [row for row in rows if row.cc_session_id not in excluded_ids]

    monkeypatch.setattr(cc_routes, "list_sessions", fake_list_sessions)
    client = _mini_app({})
    client.app.state.agent_hub = _FakeAgentHub()

    history = client.get("/api/cc/sessions", params={"workdir": str(tmp_path)})
    resume = client.post(
        "/api/cc/sessions",
        json={"workdir": str(tmp_path), "resume_from": "private-native"},
    )

    assert history.status_code == 200
    assert observed["excluded"] == frozenset({"private-native"})
    assert [item["cc_session_id"] for item in history.json()["data"]] == [
        "visible-native"
    ]
    assert history.json()["meta"]["total"] == 1
    assert resume.status_code == 404
