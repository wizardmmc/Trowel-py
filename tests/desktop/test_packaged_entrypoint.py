"""验证冻结桌面入口只分发 sidecar 和已声明的 MCP 模块。"""

import pytest

from trowel_py.desktop.packaged_entrypoint import resolve_packaged_module


def test_no_arguments_selects_desktop_sidecar() -> None:
    """Electron 的常规启动不带参数，应继续进入桌面 sidecar。"""
    assert resolve_packaged_module([]) is None


@pytest.mark.parametrize(
    "module",
    ["trowel_py.agent_mcp.server", "trowel_py.memory.mcp_server"],
)
def test_declared_mcp_module_is_allowed(module: str) -> None:
    """两种 runtime 共同声明的 MCP 模块必须能沿用现有 ``-m`` 契约。"""
    assert resolve_packaged_module(["-m", module]) == module


@pytest.mark.parametrize(
    "arguments",
    [
        ["-m", "trowel_py.cli"],
        ["-c", "print('unexpected')"],
        ["--help"],
        ["-m", "trowel_py.agent_mcp.server", "extra"],
    ],
)
def test_other_python_entrypoints_are_rejected(arguments: list[str]) -> None:
    """冻结可执行文件不能退化成任意 Python 模块或代码执行器。"""
    with pytest.raises(ValueError, match="unsupported packaged Trowel entrypoint"):
        resolve_packaged_module(arguments)
