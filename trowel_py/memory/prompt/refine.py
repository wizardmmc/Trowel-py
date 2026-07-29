"""定义 Daily review 提炼使用的枚举约束、知识轨信号、草稿结构和 prompt 模板。"""

from __future__ import annotations

# refine 草稿允许输出的三档验证状态。
VERIFICATION_TIERS = ("verified", "event-data-supported", "inferred-untested")

# refine 草稿允许输出的五类笔记。
NOTE_KINDS = ("fact", "gotcha", "procedure", "preference", "hypothesis")

# 用于提示和审计知识轨内容误入经历轨的信号词。
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

# refine agent 写入 draft.json 时必须遵守的字段结构。
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
      "items": [
        {
          "kind": "outcome",
          "summary": "完成或推进到什么可观察状态",
          "detail": "影响恢复的文件、commit、测试或失败细节；没有则为空字符串",
          "source_refs": ["L000001"]
        },
        {
          "kind": "decision",
          "summary": "做了什么选择",
          "reason": "为什么这样选",
          "status": "active | superseded",
          "source_refs": ["L000002"]
        },
        {
          "kind": "correction",
          "before": "原判断或原做法",
          "after": "更正后的判断或做法",
          "reason": "什么证据促成更正",
          "source_refs": ["L000003", "L000004"]
        },
        {
          "kind": "open_loop",
          "summary": "还没完成什么；下一步或阻塞是什么",
          "reason": "为什么仍未完成",
          "status": "active | closed",
          "source_refs": ["L000005"]
        },
        {
          "kind": "evidence",
          "summary": "影响后续判断的观测证据",
          "detail": "必要的命令、测试、错误或数值",
          "source_refs": ["L000006"]
        }
      ]
    }
  ],
  "reflection": "温故反思：有没有已存在笔记没用上导致绕弯路",
  "escalate_to_human": ["万策尽才问人的问题"]
}
"""

REFINE_PROMPT_TEMPLATE = (
    """\
你是 trowel 的「温故提炼」agent。任务：读一个已完成的会话片段，提炼出可复用知识 + 高召回经历，并对每条结论做自行验证（第 7 步是命门）。

你自动带着 trowel 的记忆注入（层一铁律 + dictionary L0 + 近期日记 + memory 根路径）——这模拟"我还记得点"。查已有笔记主动用 memory.search 工具（注入段里给了根路径和用法），别只靠注入的日记就当查过了。

【输入】
- 要提炼的 numbered JSONL 路径：{jsonl_path}
  你自己 read 这个文件（绝对路径）。每个非空原始事件前只有一个 `Lxxxxxx<TAB>` 前缀，L 编号是 source ref，TAB 后仍是原始 runtime event。
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
- 经历轨（diary）：按 item kind 结构化，不是自由流水账。每个日期可产出五类 item：
  - outcomes：完成或推进到什么可观察状态（做了什么、验证到什么程度）
  - decisions：做了什么选择 + 必要的一句理由（只在影响后续行为时记）
  - corrections：原判断/做法 -> 更正后的结论/做法（用户纠错、被证据推翻的旧判断）
  - open_loops：还没完成什么；下一步或阻塞是什么（仍有效的待办）
  - evidence：影响恢复或判断的真实观测，例如关键测试、错误、命令结果或数值
- 每项必须独立可理解，并至少引用一个直接支持它的真实 L 编号；Python 会拒绝不存在、重复或空的 source_refs。
- decision 必须保留理由；correction 必须拆成 before / after 并写促成更正的证据；open_loop 只把片段结束时仍有效的事项标 active，已完成或放弃的标 closed。
- episode 偏高召回。合并同一事实，但不要为了固定条数或字符预算提前丢掉恢复状态；文件、commit、测试、失败和阻塞只要影响恢复或判断就保留。
- 不逐轮复述工具流水，也不补写 source 外的事实。无信息时 items 输出空列表，不写"无"。
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
) -> str:
    """填充 refine 模板，并按需添加来源字节范围说明。

    先全局替换 ``{jsonl_path}``，再替换 ``{cost}``，因此路径文本中注入的成本
    占位符会继续被第二步替换，成本文本中的路径占位符不会回头替换。

    任一 offset 非 ``None`` 时添加范围说明。显示起点由 ``start_offset or 0``
    决定：``None`` 和 0 都显示为 0，负值原样显示；终点 ``None`` 显示为
    ``EOF``，其余值（包括 0 和负值）原样显示。范围只约束 agent；函数不
    读取或截取文件，也不校验路径、offset 顺序、文件边界和成本文本。默认
    ``template`` 在函数定义时绑定。

    Args:
        jsonl_path: 注入模板的 numbered JSONL 路径文本。
        cost_text: 注入模板的客观成本文本。
        start_offset: 可选的原 JSONL 起始字节偏移。
        end_offset: 可选的原 JSONL 结束字节偏移。
        template: 要填充的 refine 模板。

    Returns:
        带可选范围前缀的完整 prompt。
    """
    prompt = template.replace("{jsonl_path}", jsonl_path).replace("{cost}", cost_text)
    if start_offset is not None or end_offset is not None:
        start = start_offset or 0
        end = "EOF" if end_offset is None else end_offset
        prompt = (
            f"【来源范围】numbered 文件只包含原 jsonl 字节区间 [{start}, {end}]；"
            "该区间之前的内容已提炼过，不要补写。\n\n"
            + prompt
        )
    return prompt
