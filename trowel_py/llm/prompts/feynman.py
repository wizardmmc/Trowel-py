"""从同目录的文本文件加载费曼练习中用于出题和评估用户回答的系统提示词。"""

from pathlib import Path

_DIR = Path(__file__).parent

FEYNMAN_QUESTION_SYSTEM_PROMPT = _DIR.joinpath(
    "feynman_question_prompt.txt"
).read_text()
FEYNMAN_EVAL_SYSTEM_PROMPT = _DIR.joinpath("feynman_eval_prompt.txt").read_text()
