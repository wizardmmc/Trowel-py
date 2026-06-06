from pathlib import Path

EXTRACT_SYSTEM_PROMPT = Path(__file__).parent.joinpath("extract_card_prompt.txt").read_text()
