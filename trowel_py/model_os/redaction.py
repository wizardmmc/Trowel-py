"""对 Model OS journal payload 做确定性递归脱敏。

敏感键的标量值和已知 secret 形态会替换为带 SHA-256 摘要的 marker；结构值保留
容器并递归按子键或值形态判断。Store 在事件和决策入库前统一调用此入口。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# 键名不区分大小写；``content`` 只承载正文，reducer 不依赖该字段。
_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "session_token",
        "secret",
        "password",
        "passwd",
        "authorization",
        "auth",
        "cookie",
        "credential",
        "credentials",
        "prompt",
        "system_prompt",
        "thinking",
        "thought",
        "private_chat",
        "private_message",
        "content",
        "https_proxy",
        "http_proxy",
        "all_proxy",
        "proxy",
    }
)

# 不匹配裸长十六进制串：UUID 和内容 hash 是回放所需的结构 ID。
_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI 风格的密钥
    re.compile(r"^Bearer\s+\S"),  # bearer 令牌
    re.compile(r"^eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*"),  # JWT 凭据
    re.compile(
        r"^(https?|socks[45])://"  # 代理或私有 URL
        r"(127\.0\.0\.1|localhost|0\.0\.0\.0|::1|"
        r"192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)",
        re.IGNORECASE,
    ),
)


def _marker(value: Any) -> str:
    """生成不直接包含原值、可用于相等性审计的确定性脱敏 marker。"""

    raw = str(value)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"<redacted:sha256={digest}:len={len(raw)}>"


def _looks_like_secret_value(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def redact_payload(payload: Any) -> Any:
    """递归返回脱敏副本，不修改输入或非敏感结构。"""

    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in _SENSITIVE_KEYS:
                # 结构值保留容器形状，只在叶子处脱敏，便于审计 payload 结构。
                if isinstance(value, (dict, list)):
                    out[key] = redact_payload(value)
                else:
                    out[key] = _marker(value)
            else:
                out[key] = redact_payload(value)
        return out
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    if _looks_like_secret_value(payload):
        return _marker(payload)
    return payload
