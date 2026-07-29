"""定义 Daily compression 的条目闭集、输出 schema 和 prompt 模板。"""

from __future__ import annotations

# 渲染时 outcome/decision 合入进展，correction 和 open_loop 各自成节。
DAILY_ITEM_TYPES = ("outcome", "decision", "correction", "open_loop")

DAILY_ITEMS_SCHEMA = """\
{
  "items": [
    {
      "type": "outcome | decision | correction | open_loop",
      "text": "完整、可独立理解的一句话",
      "source": "<必须填上面某个 S1/S2… alias>"
    }
  ]
}
"""

DAILY_COMPRESS_TEMPLATE = (
    """\
你是日记压缩器。把 {date} 当天的结构化经历，压缩成可回忆的当天摘要——像人第二天需要的记忆，不像 agent 的工作复盘。

【输入】
当天各 segment 的结构化经历（每个 segment 带短 alias，下面四类可空）：
{sources_block}

【任务】
跨 segment 做语义合并、措辞压缩、重要性选择，产出 daily items。每个 item 必须带 source（只填上面某个 S1/S2… alias，原样复制），Python 会校验并映射回真实 segment id——不要填写 UUID，不要编造 source。

【三类映射】
- outcome / decision → 进展（决定只在影响后续行为时保留）
- correction → 更正（优先用"原来以为 X，现确认 Y"的对照表达）
- open_loops → 待续（当天已经解决的阻塞不要写进待续）

【硬规则】
- 同一件事在多 segment 重复出现：只保留一条最完整的，source 填其中任一即可（Python 会合并所有来源）。
- 删 agent 自评（认真检查/反复确认/表现不错/全程高价值这类绩效腔）、agent 情绪（除非反映用户真实痛点且影响后续决策）、工具调用顺序、逐轮尝试、常规测试流水、同义重复、已解决的临时阻塞。
- 每项完整、可独立理解的一句话。无信息就别产。
- 正文预算 ≤ 800 字（含标题）。三个有内容的 section 都必须至少留一条；在此前提下，更正 / 未关闭待续优先于额外的结果 / 决定。超预算时 Python 会按完整 item 删减，绝不会从一句话中间截断——所以你按重要性排序产出即可，不要为了凑短而写半句。

【输出】
只输出 JSON，严格按此 schema：
"""
    + DAILY_ITEMS_SCHEMA
    + """
不要解释，不要输出 markdown，只输出上面的 JSON 对象。
"""
)


def build_daily_compress_prompt(
    *,
    date: str,
    sources_block: str,
    template: str = DAILY_COMPRESS_TEMPLATE,
) -> str:
    """按固定顺序填充 Daily compression 模板。

    先全局替换 ``{date}``，再替换 ``{sources_block}``；日期文本中注入的来源
    占位符会继续被第二步替换，来源块中的日期占位符不会回头替换。函数不校验
    日期、来源 alias 或剩余占位符。

    默认 ``template`` 在函数定义时绑定；直接替换模块常量不会改变已绑定的
    默认值，调用方可显式传入其他模板。

    Args:
        date: 注入模板的目标日期文本。
        sources_block: 注入模板的结构化 segment 来源块。
        template: 要填充的模板。

    Returns:
        完成两步字符串替换后的 prompt。
    """
    return template.replace("{date}", date).replace(
        "{sources_block}",
        sources_block,
    )
