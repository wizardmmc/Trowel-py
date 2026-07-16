"""the profile-calibration distill prompt (slice-050).

Drives the distill cc agent through one session: read the jsonl (user messages
+ AskUserQuestion ``other``), derive profile suggestions for the five dims,
and write ``suggestions-draft.json``. Incremental dedup (C-8): the prompt
embeds the live profile + the existing suggestion queue so the agent does not
re-propose what's already there.

These are "prompt 固化" tests (test_profile_distill_prompt.py asserts the key
promises are in the text) — they guard against prompt drift, not LLM behavior
(mirrors prompt.py / test_prompt.py).
"""
from __future__ import annotations

from typing import Sequence

from trowel_py.memory.profile import _FIELD_TO_TITLE
from trowel_py.memory.types import Profile, Suggestion

#: the draft schema the agent must emit (shown verbatim in the prompt). id /
#: date / status are NOT here — the job stamps them (uuid, today, pending).
SUGGESTIONS_DRAFT_SCHEMA = """\
{
  "suggestions": [
    {
      "dimension": "ability | methodology | expression | goal | other",
      "body": "建议文本（会追加到该维度现有内容之后，不要替换）",
      "sources": ["来源依据：cc_session_id 或用户原话片段"],
      "rationale": "为什么判定这是用户的一个画像特征"
    }
  ]
}
"""

DISTILL_PROMPT_TEMPLATE = """\
你是 trowel 的「画像校准」agent。任务：读一个 cc 会话，从用户说的话里提炼关于"用户是什么样的人"的画像建议，交给用户确认采纳。

画像五维（每条建议必须归入其中一维）：
- ability（能力水平）：技术栈 / 学历背景 / 当前在啃什么
- methodology（方法论偏好）：怎么干活 / spec-first / spike 习惯 / 不许假设
- expression（表达风格）：怎么说话 / 大白话 / 禁翻译腔 / 审美偏好
- goal（长程目标）：在追什么 / 论文 / 求职 / 长期项目
- other（其他）：落不进上面四维的兜底（兴趣 / 习惯 / 作息）

【输入】
- 会话 jsonl 路径：{jsonl_path}
  你自己 read 这个文件。重点扫所有 user 消息，以及 AskUserQuestion 里用户选的 other 自定义文本——这些是"用户是什么样的人"的活信号。
- 已有画像（已经写进 profile 的，别重复给）：
{profile_summary}
- 现有建议队列（已经在排队等用户看了，别重复给）：
{suggestions_summary}

【规则】
1. 只从用户的话里推断。用户没体现的维度不要硬凑——宁缺毋滥，可能一条都不产。
2. 增量去重：对照上面的已有画像 + 现有队列，不产重复的；换个说法表达同一件事也算重复。
3. 每条建议会被追加到对应维度现有内容之后（不替换用户已写的字）。
4. 每条带 sources（指向具体 cc_session_id 或用户原话片段）和 rationale（为什么觉得这是画像点），可追溯。
5. 画像建议是给用户看的自我介绍素材，别用汇报腔。

【输出】
把结果写到当前工作目录的 suggestions-draft.json，严格按此 schema：
""" + SUGGESTIONS_DRAFT_SCHEMA + """
id / date / status 不用你管（系统自动补）。只写 suggestions-draft.json 这一个文件，不要改 memory 目录。如果这个会话实在提炼不出新画像建议，就写 {"suggestions": []}，诚实留空别凑数。完成后回复"草稿已写"。
"""


def build_distill_prompt(
    jsonl_path: str,
    existing_suggestions: Sequence[Suggestion],
    existing_profile: Profile,
    *,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> str:
    """Fill the template with the session path + live profile + queue summary.

    Uses ``str.replace`` (not ``.format``) so the JSON ``{}`` braces in the
    embedded schema are not mistaken for format placeholders (mirrors
    ``prompt.build_refine_prompt``).

    Args:
        jsonl_path: absolute path to the session jsonl for the agent to read.
        existing_suggestions: the current suggestion queue (any status) — shown
            so the agent dedups against pending + already-accepted proposals.
        existing_profile: the live profile.md — shown so the agent does not
            re-propose what the user already wrote.
        start_offset / end_offset: an incremental byte range (a resumed
            session's new turns). When given, a 【增量范围】 header is prepended
            so the agent reads the full session for context but only proposes
            for that slice.

    Returns:
        The fully filled prompt string.
    """
    prompt = (
        DISTILL_PROMPT_TEMPLATE.replace("{jsonl_path}", jsonl_path)
        .replace("{profile_summary}", _format_profile_summary(existing_profile))
        .replace(
            "{suggestions_summary}",
            _format_suggestions_summary(existing_suggestions),
        )
    )
    if start_offset is not None or end_offset is not None:
        start = start_offset or 0
        end = "EOF" if end_offset is None else end_offset
        prompt = (
            f"【增量范围】本次只为 jsonl 字节区间 [{start}, {end}] 产建议；"
            "区间之前已提炼过，不要重复。\n\n" + prompt
        )
    return prompt


def _format_profile_summary(profile: Profile) -> str:
    """Render the five dims as a bullet list; mark cold start when all empty."""
    has_any = any(str(getattr(profile, field)).strip() for field in _FIELD_TO_TITLE)
    if not has_any:
        return "- （画像为空，这是冷启动）"
    lines: list[str] = []
    for field, title in _FIELD_TO_TITLE.items():
        val = str(getattr(profile, field)).strip()
        lines.append(f"- {title}：{val if val else '（空）'}")
    return "\n".join(lines)


def _format_suggestions_summary(items: Sequence[Suggestion]) -> str:
    """Render existing suggestions as bullets so the agent can dedup against them."""
    if not items:
        return "- （队列为空）"
    lines: list[str] = []
    for s in items:
        title = _FIELD_TO_TITLE.get(s.dimension, s.dimension)
        lines.append(f"- [{title}] {s.body}")
    return "\n".join(lines)
