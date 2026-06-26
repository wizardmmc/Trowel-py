"""
system prompts for the feynman flow
"""

from pathlib import Path

_DIR = Path(__file__).parent

FEYNMAN_QUESTION_SYSTEM_PROMPT = _DIR.joinpath(
    "feynman_question_prompt.txt"
).read_text()
FEYNMAN_EVAL_SYSTEM_PROMPT = _DIR.joinpath("feynman_eval_prompt.txt").read_text()
