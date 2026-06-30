"""
system prompt for the re-explain flow (slice 021).
"""

from pathlib import Path

RE_EXPLAIN_SYSTEM_PROMPT = (
    Path(__file__).parent.joinpath("re_explain_prompt.txt").read_text()
)
