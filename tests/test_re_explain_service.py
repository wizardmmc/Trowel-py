"""Tests for re_explain service (slice 021).

Middle of the pyramid. The LLM is mocked (MagicMock returning a fixed schema);
we assert the user_prompt is built right (hint joined / omitted) and the call
uses call_type="re-explain" + ReExplainResultSchema. re_explain takes no repo,
so it cannot touch the DB by construction (invariant 6).
"""

from unittest.mock import MagicMock

from trowel_py.cards.service import re_explain
from trowel_py.schemas.re_explain import ReExplainResultSchema


def _fake_llm() -> MagicMock:
    """an LLMService mock that returns a fixed regenerated explanation."""
    llm = MagicMock()
    llm.structured_call.return_value = ReExplainResultSchema(
        explanation="a regenerated explanation that is clearly long enough"
    )
    return llm


def test_re_explain_returns_new_explanation():
    llm = _fake_llm()
    result = re_explain(
        explanation="the original explanation text here",
        title="useEffect",
        category="React",
        llm_service=llm,
    )
    assert result == "a regenerated explanation that is clearly long enough"


def test_re_explain_uses_re_explain_call_type_and_schema():
    """call_type drives the PROMPTS lookup; schema validates LLM output."""
    llm = _fake_llm()
    re_explain(
        explanation="the original explanation text here",
        title="useEffect",
        category="React",
        llm_service=llm,
    )
    llm.structured_call.assert_called_once()
    args, kwargs = llm.structured_call.call_args
    # schema passed positionally, call_type as kwarg (project convention)
    assert args[1] is ReExplainResultSchema
    assert kwargs["call_type"] == "re-explain"


def test_re_explain_joins_hint_into_prompt_when_given():
    llm = _fake_llm()
    re_explain(
        explanation="the original explanation text here",
        title="useEffect",
        category="React",
        llm_service=llm,
        user_hint="举一个真实的例子",
    )
    user_prompt = llm.structured_call.call_args.args[0]
    assert "举一个真实的例子" in user_prompt
    assert "用户希望的方向" in user_prompt


def test_re_explain_omits_hint_section_when_none():
    llm = _fake_llm()
    re_explain(
        explanation="the original explanation text here",
        title="useEffect",
        category="React",
        llm_service=llm,
    )
    user_prompt = llm.structured_call.call_args.args[0]
    assert "用户希望的方向" not in user_prompt
    # card context is still passed to the LLM
    assert "useEffect" in user_prompt


def test_re_explain_calls_llm_exactly_once():
    """invariant 6: pure generator, no extra calls or side effects."""
    llm = _fake_llm()
    re_explain(
        explanation="the original explanation text here",
        title="useEffect",
        category="React",
        llm_service=llm,
        user_hint="更通俗",
    )
    assert llm.structured_call.call_count == 1
