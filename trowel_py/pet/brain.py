"""根据宠物心情生成对话的可替换边界。"""

from __future__ import annotations

from typing import Protocol
from dataclasses import dataclass

from trowel_py.pet.types import PetMood


@dataclass(frozen=True)
class PetBrainInput:
    """`context` 预留给其他实现；模板实现有意忽略它。"""

    mood: PetMood
    context: dict[str, str] | None = None


@dataclass(frozen=True)
class PetResponse:
    text: str
    mood: PetMood


class PetBrain(Protocol):
    def generate_response(self, input: PetBrainInput, rand: float) -> PetResponse:
        """`rand` 由调用方注入，使模板选择可确定重放。"""
        ...


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
    def generate_response(self, input: PetBrainInput, rand: float) -> PetResponse:
        lines = _DIALOGUE_TEMPLATES[input.mood]
        index = min(int(rand * len(lines)), len(lines) - 1)
        return PetResponse(text=lines[index], mood=input.mood)
