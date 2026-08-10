"""验证主库 HTTP 依赖在响应发出前完成事务收尾。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi.dependencies.models import Dependant

from trowel_py.cards import routes as card_routes
from trowel_py.events import routes as event_routes
from trowel_py.feynman import routes as feynman_routes
from trowel_py.garden import routes as garden_routes
from trowel_py.pet import routes as pet_routes
from trowel_py.player import routes as player_routes
from trowel_py.review import routes as review_routes

_ROUTE_MODULES = (
    card_routes,
    event_routes,
    feynman_routes,
    garden_routes,
    pet_routes,
    player_routes,
    review_routes,
)


def _walk_dependencies(dependant: Dependant) -> Iterator[Dependant]:
    """深度优先遍历一个 route 的全部直接和间接依赖。"""

    for dependency in dependant.dependencies:
        yield dependency
        yield from _walk_dependencies(dependency)


def test_main_database_dependencies_finish_before_response_is_sent() -> None:
    """所有主库连接都应在客户端收到响应前提交并关闭。"""

    transaction_dependencies: list[Dependant] = []
    for module in _ROUTE_MODULES:
        connection_dependency = module._get_conn
        for route in module.router.routes:
            dependant: Any = getattr(route, "dependant", None)
            if dependant is None:
                continue
            transaction_dependencies.extend(
                dependency
                for dependency in _walk_dependencies(dependant)
                if dependency.call is connection_dependency
            )

    assert transaction_dependencies
    assert all(
        dependency.scope == "function" for dependency in transaction_dependencies
    )
