"""从同目录文本文件加载卡片提取系统提示词。"""

from pathlib import Path

EXTRACT_SYSTEM_PROMPT = Path(__file__).parent.joinpath("extract_card_prompt.txt").read_text()
