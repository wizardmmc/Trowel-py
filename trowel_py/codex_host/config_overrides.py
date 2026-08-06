"""把结构化 Codex 配置展开为 app-server 的 ``-c`` 参数。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _toml_scalar(value: object) -> str:
    """把受支持的 Python 标量编码成 Codex 接受的 TOML 字面量。"""

    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    raise TypeError(f"unsupported Codex override value: {type(value).__name__}")


def flatten_config_overrides(
    values: Mapping[str, Any],
    *,
    prefix: str = "",
) -> tuple[str, ...]:
    """按插入顺序把嵌套映射展开成 dotted ``key=value``。"""

    result: list[str] = []
    for key, value in values.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            result.extend(flatten_config_overrides(value, prefix=dotted))
        else:
            result.append(f"{dotted}={_toml_scalar(value)}")
    return tuple(result)


def app_server_args(values: Mapping[str, Any]) -> tuple[str, ...]:
    """返回必须放在 ``app-server`` 子命令后的 ``-c`` 参数序列。"""

    return tuple(
        item
        for override in flatten_config_overrides(values)
        for item in ("-c", override)
    )
