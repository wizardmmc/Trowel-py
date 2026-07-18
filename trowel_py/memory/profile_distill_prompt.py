"""the profile-calibration distill prompt (slice-050, v2 hard rules in 067).

Drives the distill cc agent through one session: read the jsonl (user messages
+ AskUserQuestion ``other``), derive profile suggestions for the five dims,
and write ``suggestions-draft.json``. Incremental dedup (C-8): the prompt
embeds the live profile + the existing suggestion queue so the agent does not
re-propose what's already there.

slice-067 swaps the open-ended v1 instructions for ten HARD RULES (保守归因 /
主体隔离 / 稳定性门槛 / 反证优先 / 使用价值 / 能力证据 / 目标时效 / 原子短句
/ 数量上限 / 长度上限), validated by a fixed-condition A/B on seven real
sessions (docs/experiments/profile-hard-rules-20260717/). The v1 prompt
systematically over-attributed ability (a question → "研究级") and wrote long
multi-claim bodies; v2 cut three main cases from 9 suggestions / 95.8 avg
chars to 3 / 22.7. The Python parse layer (profile_distill_job) additionally
ENFORCES the count/length/sources gates — the prompt is not the only line of
defense.

These are "prompt 固化" tests (test_profile_distill_prompt.py asserts the key
promises are in the text) — they guard against prompt drift, not LLM behavior
(mirrors prompt.py / test_prompt.py).
"""
from __future__ import annotations

from typing import Sequence

from trowel_py.memory.profile import _FIELD_TO_TITLE
from trowel_py.memory.types import Profile, Suggestion

#: the draft schema the agent must emit (shown verbatim in the prompt). id /
#: date / status / policy_version are NOT here — the job stamps them (uuid,
#: today, pending, v2).
SUGGESTIONS_DRAFT_SCHEMA = """\
{
  "suggestions": [
    {
      "dimension": "ability | methodology | expression | goal | other",
      "body": "不超过 60 个 Unicode 字符的单一结论",
      "sources": ["用户原话片段"],
      "rationale": "证据类型、归因过程、稳定性与反证检查"
    }
  ]
}
"""

DISTILL_PROMPT_TEMPLATE = """\
你是 trowel 的「画像校准」agent。任务：读一个 cc 会话，只提炼少量、稳定、会实质改变 AI 后续行为的画像建议，交给用户确认采纳。画像不是人物小传，不是对用户的赞美或能力鉴定。

画像五维（每条建议必须归入其中一维）：
- ability（能力水平）：用户明确自述的背景，或能明确归因于用户本人完成的产物所证明的能力
- methodology（方法论偏好）：用户明确说出的长期做事偏好，或跨独立场景重复出现的行为
- expression（表达风格）：用户明确要求或稳定重复的表达偏好
- goal（长程目标）：用户明确说出的长期目标
- other（其他）：落不进上面四维、但会实际改变 AI 后续行为的稳定信息

【输入】
- 会话 jsonl 路径：{jsonl_path}
  你自己 read 这个文件。重点扫所有 user 消息，以及 AskUserQuestion 里用户选的 other 自定义文本——这些是"用户是什么样的人"的活信号。
- 已有画像（已经写进 profile 的，别重复给）：
{profile_summary}
- 现有建议队列（已经在排队等用户看了，别重复给）：
{suggestions_summary}

【硬规则】
1. 保守归因：用户提问、表示不懂、要求解释、质疑某一步，只能证明正在学习或需要怎样的讲解，不能证明已经掌握该知识。不得因此写"精通""研究级""深入掌握"等能力结论。
2. 主体隔离：AI、subagent、工具完成的分析、代码、架构设计和备选方案，不得归为用户能力。用户从 AI 给出的选项中选择，也不能证明用户能独立完成该设计。
3. 稳定性门槛：一次具体选择或一次任务中的行为，不得直接写成稳定方法论。只有用户明确把它说成长期偏好，或输入中有两个独立场景重复支持，才可提炼。
4. 反证优先：同一输入里有"没看懂""不会""第一次接触"等反证时，不得输出与之冲突的高能力结论。有两种合理解释时选更保守的解释；仍不确定就不产。
5. 使用价值：只有知道这条信息后会实际改变 AI 以后"解释多深、怎么做事、怎么表达"的内容才进画像。仅仅独特、有趣或显得厉害，不够。
6. 能力证据：ability 必须来自用户明确自述，或能明确归因于用户本人完成的可核验产物；"追问得深入"不能代替能力证据。
7. 目标时效：当前任务、当前 slice、临时项目状态不得自动成为 goal 或 other；必须有长期或持续性的用户原话。
8. 原子短句：每条 body 只写一个结论，不放例子、论证、来源或人物评价；不超过 60 个 Unicode 字符。理由只放 rationale，证据只放 sources。
9. 数量上限：本会话每个输入片段最多产 2 条，按未来使用价值从高到低排列；宁缺毋滥，允许 0 条。
10. 增量去重：对照上面的已有画像 + 现有队列，不产重复的；换个说法表达同一件事也算重复。

【输出前自检】
- 这条是在描述用户，还是把 AI 的劳动算给了用户？
- 证据是在证明"会"，还是只证明"正在问"？
- 这是稳定信息，还是一次场景的偶然选择？
- 去掉例子和赞美后，是否仍能改变 AI 后续行为？

【输出】
把结果写到当前工作目录的 suggestions-draft.json，严格按此 schema：
""" + SUGGESTIONS_DRAFT_SCHEMA + """
id / date / status 不用你管（系统自动补）。只写 suggestions-draft.json 这一个文件，不要改 memory 目录。如果这个会话实在提炼不出合格建议，就写 {"suggestions": []}，诚实留空别凑数。完成后回复"草稿已写"。
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
        existing_suggestions: the current-policy suggestion queue (any status)
            — shown so the agent dedups against pending + already-accepted
            proposals. slice-067: the caller passes ONLY same-policy items; v1
            long bodies must not block a shorter, more conservative v2 proposal
            on the same theme.
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
