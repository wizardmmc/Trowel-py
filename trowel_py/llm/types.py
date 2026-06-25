"""
llm type vocabulary
"""
from __future__ import annotations
from typing import Literal

CallType = Literal[
    "extract",
    "feynman-question", # llm set question
    "feynman-eval", # based by llm
    "re-explain",
    "follow-up" # user ask question based on current card
]

DegradationStrategy = Literal[
    "queue",
    "self-eval",
    "gray-out"
]

DEGRADATION_MAP: dict[CallType, DegradationStrategy] = {
    "extract": "queue",
    "feynman-question": "gray-out",
    "feynman-eval": "self-eval", 
    "re-explain": "gray-out", 
    "follow-up": "gray-out", 
}
