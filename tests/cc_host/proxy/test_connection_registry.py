"""验证 Claude 连接级代理租约的隔离和回收。"""

from trowel_py.cc_host.proxy import ClaudeConnectionProxyRegistry


def test_connection_leases_resolve_independent_upstreams() -> None:
    """同一应用代理端口上的两个会话不能串到另一条连接。"""

    registry = ClaudeConnectionProxyRegistry()
    first = registry.acquire("https://first.example/v1/", "http://proxy-secret")
    second = registry.acquire("https://second.example/api")

    assert first != second
    assert registry.resolve(first) == "https://first.example/v1"
    assert registry.outbound_proxy(first) == "http://proxy-secret"
    assert registry.resolve(second) == "https://second.example/api"

    registry.release(first)

    assert registry.resolve(first) is None
    assert registry.outbound_proxy(first) is None
    assert registry.resolve(second) == "https://second.example/api"
