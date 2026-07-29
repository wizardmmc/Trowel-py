"""登记结构化模型调用类型对应的系统提示词。

``CallType`` 中的 ``"follow-up"`` 当前只有 ``DEGRADATION_MAP`` 映射，没有
系统提示词，不能传给 ``LLMService.structured_call()``。
"""

from trowel_py.llm.types import CallType
from trowel_py.llm.prompts.extract import EXTRACT_SYSTEM_PROMPT
from trowel_py.llm.prompts.feynman import (
    FEYNMAN_EVAL_SYSTEM_PROMPT,
    FEYNMAN_QUESTION_SYSTEM_PROMPT,
)
from trowel_py.llm.prompts.re_explain import RE_EXPLAIN_SYSTEM_PROMPT

PROMPTS: dict[CallType, str] = {
    "extract": EXTRACT_SYSTEM_PROMPT,
    "feynman-question": FEYNMAN_QUESTION_SYSTEM_PROMPT,
    "feynman-eval": FEYNMAN_EVAL_SYSTEM_PROMPT,
    "re-explain": RE_EXPLAIN_SYSTEM_PROMPT,
}
