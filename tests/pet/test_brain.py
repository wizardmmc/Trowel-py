from __future__ import annotations

import pytest

from trowel_py.pet.brain import PetBrainInput, TemplateBrain
from trowel_py.pet.types import PetMood

_HAPPY_MIDDLE = "看到你学会了新知识，我好开心！"
_EXCITED_LAST = "今天的花园格外漂亮！"


class TestRandToTemplate:
    def test_zero_rand_picks_first(self):
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="happy"), rand=0.0)
        assert resp.text == "今天又是充满收获的一天！"

    def test_mid_rand_picks_middle(self):
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="happy"), rand=0.5)
        assert resp.text == _HAPPY_MIDDLE

    def test_high_rand_picks_last(self):
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="excited"), rand=0.99)
        assert resp.text == _EXCITED_LAST

    def test_rand_one_clamps_to_last(self):
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="excited"), rand=1.0)
        assert resp.text == _EXCITED_LAST


class TestAllMoodsCovered:
    @pytest.mark.parametrize("mood", ["happy", "excited", "curious", "normal"])
    def test_every_mood_returns_a_nonempty_line(self, mood: PetMood):
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood=mood), rand=0.0)
        assert resp.text

    @pytest.mark.parametrize("mood", ["happy", "excited", "curious", "normal"])
    def test_response_echoes_input_mood(self, mood: PetMood):
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood=mood), rand=0.5)
        assert resp.mood == mood


class TestContextIgnored:
    def test_context_does_not_change_output(self):
        brain = TemplateBrain()
        without = brain.generate_response(PetBrainInput(mood="happy"), rand=0.0)
        with_context = brain.generate_response(
            PetBrainInput(mood="happy", context={"card": "abc"}), rand=0.0
        )
        assert without.text == with_context.text
