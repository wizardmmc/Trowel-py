"""为诊断信息和协议录制遮盖可识别的凭据。

本模块按字段名和有限的文本模式识别凭据，不是通用的数据脱敏器；未命中规则的
凭据，以及会话正文和本机路径中的其他敏感内容仍可能保留。recorder 写盘前调用
``redact_message()``，transport 在记录 stderr 或将其放入异常前调用
``redact_stderr()``。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

_SECRET_KEY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r".*token.*",
        r".*secret.*",
        r".*password.*",
        r".*passwd.*",
        r".*api[_-]?key.*",
        r".*auth.*",
        r".*credential.*",
        r".*bearer.*",
        r".*cookie.*",
        r".*proxy.*",  # proxy URL 可能包含 user:pass@
    )
)

# 自由文本中的已知凭据形式替换为短占位符。
_INLINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # URL 用户信息中的密码若含 ``@``，按 RFC 3986 应进行百分号编码；此模式将
    # 首个裸 ``@`` 视为用户信息终点。
    (re.compile(r"(://[^:/@\s]+):([^@/\s]+)@"), r"\1:***@"),
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 ***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        "eyJ***",
    ),
)

_REDACTED = "***REDACTED***"


def _looks_secret(key: str) -> bool:
    """检查字段名任意位置是否命中不区分大小写的凭据名称模式。"""

    return any(pattern.match(key) for pattern in _SECRET_KEY_PATTERNS)


def redact_value(value: Any) -> Any:
    """递归重建映射和列表，不修改输入。

    命中凭据名称的映射值会被整体替换；字符串会扫描已知内联模式。元组及其他
    类型不会递归处理，而是原样返回。
    """

    if isinstance(value, Mapping):
        return {
            k: (_REDACTED if _looks_secret(str(k)) else redact_value(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _redact_string(text: str) -> str:
    """遮盖已知的内联凭据形式。

    规则覆盖 URL 用户信息密码、Bearer 后的非空白串、``sk-`` 后至少 8 个
    字母数字或 ``_-`` 字符的串，以及首段以 ``eyJ`` 开头、三段剩余部分均至少
    8 个同类字符的串。
    """

    redacted = text
    for pattern, replacement in _INLINE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_message(message: Any) -> Any:
    """递归遮盖消息中映射和列表内的已知凭据。"""

    return redact_value(message)


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """复制环境映射，并整值替换名称命中凭据模式的变量。

    名称未命中的变量值不会再扫描内联凭据模式。
    """

    return {
        key: (_REDACTED if _looks_secret(key) else value) for key, value in env.items()
    }


def redact_stderr(text: str) -> str:
    """遮盖 stderr 中命中已知文本模式的凭据，保留其余内容。"""

    return _redact_string(text)
