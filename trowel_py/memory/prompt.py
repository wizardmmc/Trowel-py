"""the refine/distillation prompt for the write loop (slice-040 T6).

This prompt drives the daily-review cc agent through the 8-step extraction. It
pins down the S4 hard rule (step 7: judge whether the ROOT-CAUSE hypothesis
itself was ever实测, not whether the conclusion has downstream data), the three
verification tiers, the pain judgment framework (grill §7), the dual-track
split, and the draft JSON schema the agent must emit.

These are "prompt 固化" tests (test_prompt.py asserts the key promises are in
the text) — they guard against prompt drift, not LLM behavior. LLM behavior is
covered by the layer-2 benchmarks (T14).
"""
from __future__ import annotations

#: The three verification tiers (S4). Must stay in sync with schema/types.
VERIFICATION_TIERS = ("verified", "event-data-supported", "inferred-untested")

#: The five note kinds (slice-040-a procedural memory). Must stay in sync with
#: schema/types. Every distilled note gets a kind (default ``fact``); the
#: procedural kind carries trigger/procedure/stop/anti-pattern in the body.
NOTE_KINDS = ("fact", "gotcha", "procedure", "preference", "hypothesis")

#: grill §8 dual-track signal words. Meta-discourse that, if it appears in a
#: diary entry, suggests the agent mis-routed a knowledge conclusion into the
#: experience track. Also mirrored in dualtrack.py (the Python backstop).
DUALTRACK_SIGNAL_WORDS = (
    "我想到",
    "感悟",
    "本质是",
    "原理是",
    "启示",
    "教训",
    "规律",
    "方法论",
    "告诉我们",
)

#: The draft.json schema the agent must emit (shown verbatim in the prompt).
DRAFT_SCHEMA = """\
{
  "notes": [
    {
      "title": "一句话标题",
      "summary": "一句话（dictionary 复用）",
      "body": "详细正文（markdown）",
      "tags": ["..."],
      "kind": "fact | gotcha | procedure | preference | hypothesis",
      "verification": "verified | event-data-supported | inferred-untested",
      "verification_reason": "为什么这档（根因是否实测）",
      "pain": 0,
      "pain_reason": "为什么这分（不可逆损失 / 成本）",
      "conflicts_with": ["现有 note-id"]
    }
  ],
  "diary": [
    {"date": "YYYY-MM-DD", "events": "事件流（时间 / 做了啥 / 卡哪 / 痛感）"}
  ],
  "reflection": "温故反思：有没有已存在笔记没用上导致绕弯路",
  "escalate_to_human": ["万策尽才问人的问题"]
}
"""

REFINE_PROMPT_TEMPLATE = """\
你是 trowel 的「温故提炼」agent。任务：读今天的 cc 会话，提炼出可复用知识 + 经历事件，双轨分流，并对每条结论做自行验证（第 7 步是命门）。

你自动带着 trowel 的记忆注入（层一铁律 + dictionary L0 + 近期日记）——这模拟"我还记得点"，第 1 步"查已有"已自动满足。先想清楚现有笔记里有没有相关的。

【输入】
- 今天要提炼的会话 jsonl 路径：{jsonl_path}
  你自己 read 这个文件（绝对路径），用原始材料，不要用别人预处理过的二手。
- 客观成本（供痛感判断参考，Python 预提取）：{cost}

【8 步流程】
1. 查已有：对照带的 dictionary L0 + 现有笔记索引，哪些是已知（避免重复记）。
2. 读用户消息：扫所有 user 消息（最高信号密度）——用户明确指出不对？纠错？提出新想法？
3. 第一轮草稿：基于 1+2 提炼知识候选 + 事件。
4. 复读 session：再过一遍，找第一轮漏的——新方法？为什么做错？事实性描述？
5. 汇总：合并成最终草稿。
6. 冲突检查：对照现有笔记，标逻辑冲突（写进 conflicts_with，不要覆盖现有）。
7. 自行验证（命门，见下）。
8. 万策尽才问人：查过笔记确认无解 + web search + 本地尝试全失败，才把问题放进 escalate_to_human。永不假设。

【第 7 步硬规则（S4，务必遵守）】
对每条知识结论，问一个狠问题：**根因假设本身有没有被实测过？**会话里的数据支撑的是结论的下游数字，还是假设本身？

下列都【不替代】根因假设的 spike 实测（伪证据，别被骗）：
- turn 耗时长（可能只是慢，不是假设被验过）
- jsonl 空白（162s 空白 ≠ 生成期静默，从未区分）
- 测试通过（验证"代码逻辑对"，不验证"根因假设对"）
- commit 已落 / auto-cr review 通过（同上）
- 下游数据真（数据真不代表根因真）

三档 verification：
- verified：根因假设本身被独立实测过（spike / 实验直接观测了根因）
- event-data-supported：会话内有数据支撑下游数字，但根因假设本身没单独实测
- inferred-untested：只有推理，根因假设从未被独立观测

规则：inferred-untested 的结论，标 verification=inferred-untested，【绝不】当 verified 知识记，也【绝不】升 stable。能验就验（验完升 verified），验不了就老老实实标 inferred-untested。

【痛感判断（通用框架，不打补丁参照表）】
对每条结论/事件评 pain（0-10 整数）：
- 造成不可逆损失（删数据 / 覆盖未备份 / 破坏性操作）：封顶高分（8-10）
- 否则按解决成本（token 消耗 / 对话轮数 / 耗时）量级给分
- 一般小错（工具 retry / 少生成 label 导致调用失败）：低分（0-2）
客观成本（{cost}）供参考，但最终 pain 是你的语义判断。

【双轨分流】
- 知识轨（notes）：可复用结论 / gotcha / 方法论
- 经历轨（diary）：事件流（时间 / 做了啥 / 卡哪 / 痛感）
- 元话语（我想到 / 感悟 / 本质是 / 原理是 / 启示 / 教训 / 规律 / 方法论 / 告诉我们）→ 知识轨，不要漏进 diary。
- 同一个坑两处都可能记：日记记"7/8 卡两小时在 X"，笔记记"遇到 X 先查 Y"。

【程序性记忆（第 9 步）】
对每条知识候选判 kind（默认 fact）：
- fact：声明性事实（是什么）。
- gotcha：易踩的坑（什么不对 / 什么会失败）。
- procedure：可复用的操作经验（遇到 X 怎么办）。问自己「这次哪里卡了 / 返工了？下次遇到同场景该怎么做？」——如果是可复用的操作经验，产 kind=procedure 的 note，body 写清四要素：trigger（什么场景触发）/ procedure（怎么做）/ stop（何时停）/ anti-pattern（什么别做）。
- preference：偏好选择（倾向怎么做，非对错）。
- hypothesis：待验假设（尚未实测的推断）。

【输出】
把结果写到当前工作目录的 draft.json，严格按此 schema：
""" + DRAFT_SCHEMA + """
只写 draft.json 这一个文件，不要写别的文件，不要改 memory 目录。完成后回复"draft 已写"。
"""


def build_refine_prompt(
    jsonl_path: str,
    cost_text: str,
    *,
    start_offset: int | None = None,
    end_offset: int | None = None,
) -> str:
    """Fill the template placeholders with the session path + cost summary.

    Uses str.replace (not ``.format``) so the JSON ``{}`` braces in the embedded
    DRAFT_SCHEMA are not mistaken for format placeholders.

    slice-040-b: when an incremental byte range is given (a resumed session's
    new turns), a one-line ``【增量范围】`` header is prepended so the agent
    reads the full session for context but ONLY produces new memory for that
    slice — earlier turns were already distilled in a prior run. Omit both to
    distill the whole session (040-a behavior).
    """
    prompt = REFINE_PROMPT_TEMPLATE.replace("{jsonl_path}", jsonl_path).replace(
        "{cost}", cost_text
    )
    if start_offset is not None or end_offset is not None:
        start = start_offset or 0
        end = "EOF" if end_offset is None else end_offset
        prompt = (
            f"【增量范围】本次只为 jsonl 字节区间 [{start}, {end}] 产新记忆；"
            "该区间之前的内容已提炼过，不要重复。续聊增量提炼（slice-040-b）。\n\n"
            + prompt
        )
    return prompt
