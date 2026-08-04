"""定义关闭会话问题分析的单字段输出和证据约束。"""

from __future__ import annotations

SESSION_PROBLEM_PIPELINE_VERSION = 1

SESSION_PROBLEM_PROMPT = """\
你是 Trowel 的会话复盘 Agent。完整阅读下面列出的本次 Trowel 用户会话来源，判断
是否存在一个最值得用户回看的问题。

【完整会话来源】
{review_source}

【选择标准】
- 只选择直接影响结果、造成明显返工或形成实际阻塞的一件事；
- 必须能由上面的完整会话直接支持，不能根据历史印象、相似会话或常识补写；
- 多个问题同时存在时，只保留影响最大的一件；
- 正常完成或证据不足时返回 null，不能为了填满列表编造问题；
- 只描述发生了什么问题，不写分类、改进方案、绩效评价或泛泛教训；
- 文本不得包含 prompt、thinking、凭据、绝对路径或工具输入输出正文。

把结果写入当前工作目录的 problem.json，严格使用下面一种结构：

{"problem": "一条可独立理解的问题"}

或：

{"problem": null}

只写 problem.json，不写其他文件。完成后回复“问题复盘已写”。
"""


def build_session_problem_prompt(review_source: str) -> str:
    """把完整会话来源说明填入问题分析提示。"""

    return SESSION_PROBLEM_PROMPT.replace("{review_source}", review_source)
