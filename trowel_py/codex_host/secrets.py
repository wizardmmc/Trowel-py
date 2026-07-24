"""为诊断与原始录制脱敏 secret 和环境变量。

auth、token、含凭据的 proxy 字符串和完整环境变量不能进入日志或 fixture。recorder
写盘前调用 ``redact_message``，transport 将 stderr 放入异常前调用
``redact_stderr``。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# key 匹配不区分大小写，命中后整值替换。
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

# 自由文本中的凭据替换为短占位符，保留结构但不保留值。
_INLINE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # URL password 必须按 RFC 3986 编码裸 ``@``，这里以首个 ``@`` 结束 userinfo。
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
    return any(pattern.match(key) for pattern in _SECRET_KEY_PATTERNS)


def redact_value(value: Any) -> Any:
    """递归脱敏并新建容器，不修改输入对象。"""

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
    redacted = text
    for pattern, replacement in _INLINE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_message(message: Any) -> Any:
    """返回保持 JSON-RPC 结构的脱敏副本，供录制与诊断使用。"""

    return redact_value(message)


def redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """复制环境映射，整值替换命中 secret key 的条目。"""

    return {
        key: (_REDACTED if _looks_secret(key) else value) for key, value in env.items()
    }


def redact_stderr(text: str) -> str:
    """移除即将进入异常消息的 stderr 片段中的内联凭据。"""

    return _redact_string(text)
