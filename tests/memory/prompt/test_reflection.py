"""每日 review 的 recall 反思 prompt 契约。"""

from __future__ import annotations

from trowel_py.memory.reflection import REFLECTION_PROMPT_TEMPLATE


def test_reflection_template_pins_the_question() -> None:
    assert "已存在笔记" in REFLECTION_PROMPT_TEMPLATE
    assert "绕弯路" in REFLECTION_PROMPT_TEMPLATE
    assert "召回 miss" in REFLECTION_PROMPT_TEMPLATE
    assert "新颖问题" in REFLECTION_PROMPT_TEMPLATE
