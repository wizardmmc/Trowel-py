"""LLM 调用类型与降级策略的稳定值域。"""

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
