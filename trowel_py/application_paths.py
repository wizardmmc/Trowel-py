"""解析 Trowel 自有持久化数据的统一根目录。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

APPLICATION_DATA_ROOT_ENV = "TROWEL_DATA_ROOT"


def has_application_data_root_override(
    environment: Mapping[str, str] | None = None,
) -> bool:
    """判断当前进程是否显式指定了 Trowel 数据根目录。

    Args:
        environment: 要检查的环境映射；省略时读取当前进程环境。

    Returns:
        环境变量存在且去除首尾空白后非空时为 ``True``。
    """
    source = os.environ if environment is None else environment
    return bool(source.get(APPLICATION_DATA_ROOT_ENV, "").strip())


def resolve_application_data_root(
    environment: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    """返回 Trowel 数据库、Memory 和本地索引共同使用的根目录。

    桌面 Host 通过 ``TROWEL_DATA_ROOT`` 指定系统应用数据目录。浏览器和命令行
    未设置该变量时继续使用既有的 ``~/.trowel``。本函数不创建目录，也不修改
    ``HOME``，因此 Claude Code 与 Codex 仍能读取各自的用户安装和历史。

    Args:
        environment: 查找覆盖变量使用的环境映射；省略时读取当前进程环境。
        home: 未配置覆盖时使用的用户主目录；测试可传隔离路径。

    Returns:
        展开 ``~`` 后的数据根目录。
    """
    source = os.environ if environment is None else environment
    override = source.get(APPLICATION_DATA_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return (Path.home() if home is None else home) / ".trowel"
