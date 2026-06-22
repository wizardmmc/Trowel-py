"""
pure-function tests for the pet brain — no db.

the brain is the swappable "personality": mood in, one spoken line out.
these tests are the regression guard — they lock down:
  - rand -> template index mapping (so a mood never silently picks the wrong line)
  - every mood bucket is reachable (so a typo'd mood key stays caught)
  - context is genuinely ignored by TemplateBrain (its documented contract)
"""
from __future__ import annotations

import pytest

from trowel_py.pet.brain import PetBrainInput, TemplateBrain
from trowel_py.pet.types import PetMood

# template lines mirrored from brain.py only to assert the EXACT line at a known
# index — keeps the test honest about what "index 0 / last" means.
# trade-off: editing a template sentence requires editing here too. that's fine:
# the wording is product content and deserves a review when it changes.
_HAPPY_MIDDLE = "看到你学会了新知识，我好开心！"
_EXCITED_LAST = "今天的花园格外漂亮！"


class TestRandToTemplate:
    def test_zero_rand_picks_first(self):
        # rand 0.0 -> index 0 -> the first template of the mood
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="happy"), rand=0.0)
        assert resp.text == "今天又是充满收获的一天！"

    def test_mid_rand_picks_middle(self):
        # happy has 3 lines: 0.5 * 3 = 1.5 -> int 1 -> the middle line
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="happy"), rand=0.5)
        assert resp.text == _HAPPY_MIDDLE

    def test_high_rand_picks_last(self):
        # excited has 2 lines: 0.99 * 2 = 1.98 -> int 1 -> last line
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="excited"), rand=0.99)
        assert resp.text == _EXCITED_LAST

    def test_rand_one_clamps_to_last(self):
        # defensive: a caller handing rand == 1.0 must not index out of range.
        # this is the clamp guard line — delete it and this blows up.
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood="excited"), rand=1.0)
        assert resp.text == _EXCITED_LAST


class TestAllMoodsCovered:
    @pytest.mark.parametrize("mood", ["happy", "excited", "curious", "normal"])
    def test_every_mood_returns_a_nonempty_line(self, mood: PetMood):
        # if a mood key were misspelled in the template table, this raises KeyError
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood=mood), rand=0.0)
        assert resp.text  # non-empty

    @pytest.mark.parametrize("mood", ["happy", "excited", "curious", "normal"])
    def test_response_echoes_input_mood(self, mood: PetMood):
        # invariant: the spoken line is labeled with the mood that produced it
        brain = TemplateBrain()
        resp = brain.generate_response(PetBrainInput(mood=mood), rand=0.5)
        assert resp.mood == mood


class TestContextIgnored:
    def test_context_does_not_change_output(self):
        # TemplateBrain's documented contract: context is accepted but unused.
        # same mood + same rand must yield the same line whether context is set or not.
        brain = TemplateBrain()
        without = brain.generate_response(PetBrainInput(mood="happy"), rand=0.0)
        with_context = brain.generate_response(
            PetBrainInput(mood="happy", context={"card": "abc"}), rand=0.0
        )
        assert without.text == with_context.text
