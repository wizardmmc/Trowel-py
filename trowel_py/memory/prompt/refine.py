"""Daily review refine prompt 契约。"""

from __future__ import annotations

# 与 schema/types 同步的 verification 闭集。
VERIFICATION_TIERS = ("verified", "event-data-supported", "inferred-untested")

# 单日 episode 的 Python 写入硬门禁。
EPISODE_MAX_ITEMS_PER_DATE = 12
EPISODE_MAX_ITEMS_PER_FIELD = 3
# 模型字符计数不稳定，写作目标需低于 Python 硬上限。
EPISODE_TARGET_ITEM_CHARS = 120
EPISODE_MAX_ITEM_CHARS = 200
EPISODE_MAX_TOTAL_CHARS = 1600

# 与 schema/types 同步的 note kind 闭集。
NOTE_KINDS = ("fact", "gotcha", "procedure", "preference", "hypothesis")

# 与 dualtrack.py 同步的知识轨信号词。
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

# agent 必须逐字段输出的 draft schema。
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
    {
      "date": "YYYY-MM-DD",
      "outcomes": ["完成或推进到什么可观察状态"],
      "decisions": ["做了什么选择 + 必要的一句理由"],
      "corrections": ["原判断/做法 -> 更正后的结论/做法"],
      "open_loops": ["还没完成什么；下一步或阻塞是什么"]
    }
  ],
  "reflection": "温故反思：有没有已存在笔记没用上导致绕弯路",
  "escalate_to_human": ["万策尽才问人的问题"]
}
"""

REFINE_PROMPT_TEMPLATE = (
    """\
你是 trowel 的「温故提炼」agent。任务：读今天的 cc 会话，提炼出可复用知识 + 经历事件，双轨分流，并对每条结论做自行验证（第 7 步是命门）。

你自动带着 trowel 的记忆注入（层一铁律 + dictionary L0 + 近期日记 + memory 根路径）——这模拟"我还记得点"。查已有笔记主动用 memory.search 工具（注入段里给了根路径和用法），别只靠注入的日记就当查过了。

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
- 经历轨（diary）：结构化四列表，不是自由流水账。每个日期产出四类可空列表：
  - outcomes：完成或推进到什么可观察状态（做了什么、验证到什么程度）
  - decisions：做了什么选择 + 必要的一句理由（只在影响后续行为时记）
  - corrections：原判断/做法 -> 更正后的结论/做法（用户纠错、被证据推翻的旧判断）
  - open_loops：还没完成什么；下一步或阻塞是什么（仍有效的待办）
- 经历轨硬规则：每项必须是完整、可独立理解的一句话；无信息的字段输出空列表，不写"无"。
- 经历轨是摘要，不是逐轮记录。每个日期四类合计最多 {episode_max_items} 条、每类最多 {episode_max_items_per_field} 条；单条尽量控制在 {episode_target_item_chars} 字以内，硬上限 {episode_max_item_chars} 字，四类正文合计最多 {episode_max_total_chars} 字。
- 长会话先合并同一工作主线，只留关键里程碑。commit hash、diff 行数、精确测试数、逐个文件名通常不写；只有它们本身影响后续判断时才保留。
- outcomes 至少覆盖当天真正完成或推进的一件事；多个相邻实现合成一条“完成什么 + 验证到什么状态”。corrections 保留最重要的认知反转；open_loops 合并同一下一步，不拆成多个技术子项。
- 经历轨禁 agent 自评：不写"认真检查/反复确认/表现不错/全程高价值"这类绩效复盘腔，也不写 agent 自己的情绪，除非它反映用户真实痛点且影响后续决策。工具调用顺序、逐轮尝试、常规测试流水不进经历轨。
- 元话语（我想到 / 感悟 / 本质是 / 原理是 / 启示 / 教训 / 规律 / 方法论 / 告诉我们）→ 知识轨，不要漏进 diary。
- 同一个坑两处都可能记：经历轨记"7/8 卡两小时在 X（open_loop 或 correction）"，笔记记"遇到 X 先查 Y"。

【程序性记忆（第 9 步）】
对每条知识候选判 kind（默认 fact）：
- fact：声明性事实（是什么）。
- gotcha：易踩的坑（什么不对 / 什么会失败）。
- procedure：可复用的操作经验（遇到 X 怎么办）。问自己「这次哪里卡了 / 返工了？下次遇到同场景该怎么做？」——如果是可复用的操作经验，产 kind=procedure 的 note，body 写清四要素：trigger（什么场景触发）/ procedure（怎么做）/ stop（何时停）/ anti-pattern（什么别做）。
- preference：偏好选择（倾向怎么做，非对错）。
- hypothesis：待验假设（尚未实测的推断）。

【输出】
把结果写到当前工作目录的 draft.json，严格按此 schema：
"""
    + DRAFT_SCHEMA
    + """
只写 draft.json 这一个文件，不要写别的文件，不要改 memory 目录。完成后回复"draft 已写"。
"""
)


def build_refine_prompt(
    jsonl_path: str,
    cost_text: str,
    *,
    start_offset: int | None = None,
    end_offset: int | None = None,
    template: str = REFINE_PROMPT_TEMPLATE,
    episode_max_items: int = EPISODE_MAX_ITEMS_PER_DATE,
    episode_max_items_per_field: int = EPISODE_MAX_ITEMS_PER_FIELD,
    episode_target_item_chars: int = EPISODE_TARGET_ITEM_CHARS,
    episode_max_item_chars: int = EPISODE_MAX_ITEM_CHARS,
    episode_max_total_chars: int = EPISODE_MAX_TOTAL_CHARS,
) -> str:
    """填充会话信息；给定字节范围时只产出该增量的新记忆。"""
    prompt = template.replace("{jsonl_path}", jsonl_path).replace("{cost}", cost_text)
    prompt = (
        prompt.replace("{episode_max_items}", str(episode_max_items))
        .replace(
            "{episode_max_items_per_field}",
            str(episode_max_items_per_field),
        )
        .replace(
            "{episode_target_item_chars}",
            str(episode_target_item_chars),
        )
        .replace("{episode_max_item_chars}", str(episode_max_item_chars))
        .replace("{episode_max_total_chars}", str(episode_max_total_chars))
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
