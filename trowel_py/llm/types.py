"""定义结构化模型调用使用的提示词类型及其降级方式。

``DEGRADATION_MAP`` 目前没有代码读取，因此不会影响运行时行为。
``CallType`` 虽包含 ``"follow-up"``，但 ``PROMPTS`` 未登记对应的系统提示词；
将其传给 ``LLMService.structured_call()`` 会触发 ``KeyError``。
"""

from __future__ import annotations

from typing import Literal

CallType = Literal[
    "extract",
    "feynman-question",
    "feynman-eval",
    "re-explain",
    "follow-up",
]

DegradationStrategy = Literal["queue", "self-eval", "gray-out"]

DEGRADATION_MAP: dict[CallType, DegradationStrategy] = {
    "extract": "queue",
    "feynman-question": "gray-out",
    "feynman-eval": "self-eval",
    "re-explain": "gray-out",
    "follow-up": "gray-out",
}
