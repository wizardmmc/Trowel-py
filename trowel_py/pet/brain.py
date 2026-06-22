"""
pet brain: the swappable "personality" that turns a mood into a spoken line
"""
from __future__ import annotations
from typing import Protocol
from dataclasses import dataclass

from trowel_py.pet.types import PetMood

@dataclass(frozen=True)
class PetBrainInput:
    """
    what the brain is asked to react to

    Attributes:
        mood: the pet's current mood, picks the template bucket.
        context: extra info (e.g. card just learned) an LLM brain could use;
                 the template brain ignores it. kept optional so callers don't have to build it.
    """
    mood: PetMood
    context: dict[str, str] | None = None

@dataclass(frozen=True)
class PetResponse:
    """
    one spoken line.

    Attributes:
        text: the spoken sentence.
        mood: the mood this line expresses (echoes input mood for the caller).
    """
    text: str
    mood: PetMood

class PetBrain(Protocol):
    """
    the contract every pet personality implements
    """
    def generate_response(self, input: PetBrainInput, rand: float) -> PetResponse:
        """
        produce one spoken line for the given mood

        Args:
            input: what to react to (mood + context).
            rand: a [0, 1) float driving which template is picked; injected so the
                  choice is deterministic under test.

        Returns:
            one PetResponse (text + mood).
        """
        ...

# immutable
_DIALOGUE_TEMPLATES: dict[PetMood, tuple[str, ...]] = {
    "happy": (
        "今天又是充满收获的一天！",
        "看到你学会了新知识，我好开心！",
        "花园里的植物们都在茁壮成长呢！",
    ),
    "excited": (
        "太棒了！连续学习的感觉真好！",
        "今天的花园格外漂亮！",
    ),
    "curious": (
        "这个知识点真有趣，你能再给我讲讲吗？",
        "我发现了一个新问题想问你！",
    ),
    "normal": (
        "今天想学点什么呢？",
        "随时可以开始复习哦！",
        "花园里有些植物需要浇水了呢。",
    ),
}

class TemplateBrain:
    """
    the zero-cost brain: pick a template line for the mood, ignore context
    """
    def generate_response(self, input: PetBrainInput, rand: float) -> PetResponse:
        """
        pick one template for input.mood using rand

        Args:
            input: mood (+ ignored context).
            rand: a [0, 1) float; scaled to a template index.

        Returns:
            one PetResponse whose text comes from the mood's template bucket.
        """
        lines = _DIALOGUE_TEMPLATES[input.mood]
        index = min(int(rand * len(lines)), len(lines) - 1)
        return PetResponse(text=lines[index], mood=input.mood)
    