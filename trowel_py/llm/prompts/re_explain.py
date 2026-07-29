"""从同目录文本文件加载为知识卡片重新生成候选解释的系统提示词。"""

from pathlib import Path

RE_EXPLAIN_SYSTEM_PROMPT = (
    Path(__file__).parent.joinpath("re_explain_prompt.txt").read_text()
)
