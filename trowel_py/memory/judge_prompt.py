"""判效 agent 的结构化输出 schema 与提示词模板。"""

from __future__ import annotations

# novelty 代表需要写入新知识，不属于检索或意识漏召回。
MISS_ATTRIBUTIONS = ("retrieval_miss", "awareness_miss")

# 与 access_log.Outcome 共用词表，避免结构化日志和判效结果出现分叉。
HIT_OUTCOMES = ("helpful", "harmful", "unused", "unknown")

JUDGE_SCHEMA = """\
{
  "hits": [
    {
      "memory_id": "真实存在的笔记 id",
      "used": true,
      "outcome": "helpful | harmful | unused | unknown",
      "reason": "为什么这么判（用了没用 + 有用没用）",
      "evidence": "会话里哪一步佐证"
    }
  ],
  "recall_miss": [
    {
      "memory_id": "真实存在的笔记 id",
      "attribution": "retrieval_miss | awareness_miss",
      "reason": "为什么这条该用却没用",
      "evidence": "会话里哪一步本可避免绕弯"
    }
  ],
  "summary": "一句话总结这个会话的记忆使用情况"
}
"""

JUDGE_PROMPT_TEMPLATE = (
    """\
你是 trowel 的「判效」agent。任务：读一个 cc 会话，判断这个会话里 trowel 的笔记**用了没用、用了有没有用、有没有该用却没用**。

你自动带着 trowel 的记忆注入（层一铁律 + dictionary L0 + 近期日记 + memory 根路径）。判断「该用没用」时，可以主动用 memory.search 验证某条笔记当时能不能搜到——但**你判断的是被评判会话当时的情况，不是你现在搜出来的情况**。

【输入】
- 被评判会话 jsonl 路径：{jsonl_path}
  你自己 read 这个文件（绝对路径），看会话经过。
- 该会话的检索记录（Python 预提取的硬证据，按被评判会话的 cc_session_id 过滤，不是你自己 search 产生的）：
{access_log_summary}
  这是这个会话当时 search 了哪些 query、read 了哪些笔记的客观记录。
- 已存在笔记索引（dictionary L0）：
{dictionary_index}

【三维度判断】
① **用了没用（used）**：模型有没有把这条笔记的内容融进决策（引用了 / 照着做了 / 基于它改了方向）。光 read 不算用，要落到动作上。

② **有用没用（outcome）**：
  - helpful：用了，且帮到了（少走弯路 / 避坑 / 加快）
  - harmful：用了，但带偏了（照着错的笔记做）
  - unused：没用（read 了或搜到了但没融进决策）
  - unknown：给不出判断

③ **该用没用（recall-miss，带归因）**：扫整个会话，有没有「当时有相关笔记却没用上、导致绕弯路」的？每条给归因：
  - retrieval_miss：当时根本没搜到（检索 / dictionary 没召回）
  - awareness_miss：搜到 / 注入了但没意识到能用（注入 / 意识问题）
  - 当时确实没相关笔记（新颖问题，该写入）→ **不算 miss**，不要写进 recall_miss。

【硬规则】
- 每条判断必须带 reason（为什么这么判）+ evidence（会话里哪一步佐证）。给不出证据就别硬编（C-4 可追溯）。
- memory_id 必须是上面索引里真实存在的 id。**不许编造** memory_id——编造的会被 Python 丢弃（C-6）。判断 recall-miss 前先确认那条笔记真存在。
- 只基于会话与检索记录里的客观事实判断，不臆测。

【输出】
把结果写到当前工作目录的 judgement-draft.json，严格按此 schema：
"""
    + JUDGE_SCHEMA
    + """
只写 judgement-draft.json 这一个文件，不要改 memory 目录。完成后回复「判效已写」。
"""
)


def build_judge_prompt(
    jsonl_path: str,
    access_log_summary: str,
    dictionary_index: str,
) -> str:
    """注入路径、检索证据和索引；逐项替换避免 JSON 花括号被解析为占位符。"""
    return (
        JUDGE_PROMPT_TEMPLATE.replace("{jsonl_path}", jsonl_path)
        .replace("{access_log_summary}", access_log_summary)
        .replace("{dictionary_index}", dictionary_index)
    )
