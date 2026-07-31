"""构造 Profile 提炼 agent 使用的画像建议指令。

模板属于机器契约；Python gate 另行强制数量、长度和来源约束。
"""

from __future__ import annotations

from typing import Sequence

from trowel_py.profile.document import _FIELD_TO_TITLE
from trowel_py.profile.distill.sources.models import ProfileDistillSource
from trowel_py.profile.distill.sources.render import render_profile_source
from trowel_py.profile.models import Profile, Suggestion

# Schema 只指导 agent 输出；gate 补齐 id、date、status 和策略版本，
# 并且不保存 rationale。
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

DISTILL_PROMPT_TEMPLATE = (
    """\
你是 trowel 的「画像校准」agent。任务：读一份用户与 coding agent 的会话来源，只提炼少量、稳定、会实质改变 AI 后续行为的画像建议，交给用户确认采纳。画像不是人物小传，不是对用户的赞美或能力鉴定。

画像五维（每条建议必须归入其中一维）：
- ability（能力水平）：用户明确自述的背景，或能明确归因于用户本人完成的产物所证明的能力
- methodology（方法论偏好）：用户明确说出的长期做事偏好，或跨独立场景重复出现的行为
- expression（表达风格）：用户明确要求或稳定重复的表达偏好
- goal（长程目标）：用户明确说出的长期目标
- other（其他）：落不进上面四维、但会实际改变 AI 后续行为的稳定信息

【输入】
- 会话来源：
{source_description}
  你自己 read 上述文件和区间。重点扫所有 user 消息，以及 AskUserQuestion 里用户选的 other 自定义文本——这些是"用户是什么样的人"的活信号。
  context 只帮助理解指代和前因后果；建议证据只能来自 target 中的真实用户输入。
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
"""
    + SUGGESTIONS_DRAFT_SCHEMA
    + """
id / date / status 不用你管（系统自动补）。只写 suggestions-draft.json 这一个文件，不要改 memory 目录。如果这个会话实在提炼不出合格建议，就写 {"suggestions": []}，诚实留空别凑数。完成后回复"草稿已写"。
"""
)


def build_source_distill_prompt(
    source: ProfileDistillSource,
    existing_suggestions: Sequence[Suggestion],
    existing_profile: Profile,
) -> str:
    """把统一来源、当前 Profile 和建议队列摘要注入提炼模板。

    函数不筛选调用方提供的建议；policy version 或状态由调用方负责。来源路径
    只写进 prompt，由 Agent 自主读取，Python 不复制或拼接 transcript。

    Args:
        source: 已区分 context 和 target 的运行时无关来源。
        existing_suggestions: 作为去重上下文注入的当前策略建议。
        existing_profile: 作为去重上下文注入的当前五维 Profile。

    Returns:
        可直接发送给 Profile 提炼 Agent 的完整提示。
    """
    return (
        DISTILL_PROMPT_TEMPLATE.replace(
            "{source_description}",
            render_profile_source(source),
        )
        .replace("{profile_summary}", _format_profile_summary(existing_profile))
        .replace(
            "{suggestions_summary}",
            _format_suggestions_summary(existing_suggestions),
        )
    )


def _format_profile_summary(profile: Profile) -> str:
    """把现有五维 Profile 格式化为去重上下文。

    五个维度去掉首尾空白后均为空时只返回冷启动标记；否则按固定顺序列出全部
    维度，空维度显示“（空）”。``updated`` 和 ``source`` 不进入摘要。

    Args:
        profile: 要摘要的当前 Profile。

    Returns:
        可直接插入提示的项目符号文本。
    """
    has_any = any(str(getattr(profile, field)).strip() for field in _FIELD_TO_TITLE)
    if not has_any:
        return "- （画像为空，这是冷启动）"
    lines: list[str] = []
    for field, title in _FIELD_TO_TITLE.items():
        val = str(getattr(profile, field)).strip()
        lines.append(f"- {title}：{val if val else '（空）'}")
    return "\n".join(lines)


def _format_suggestions_summary(items: Sequence[Suggestion]) -> str:
    """把调用方提供的建议按原顺序格式化为去重上下文。

    空序列返回队列为空标记。已知维度显示中文标题，未知维度回退到原值；正文
    原样拼接，不包含 ID、来源、日期、状态或策略版本。

    Args:
        items: 要展示的既有建议。

    Returns:
        可直接插入提示的项目符号文本。
    """
    if not items:
        return "- （队列为空）"
    lines: list[str] = []
    for s in items:
        title = _FIELD_TO_TITLE.get(s.dimension, s.dimension)
        lines.append(f"- [{title}] {s.body}")
    return "\n".join(lines)
