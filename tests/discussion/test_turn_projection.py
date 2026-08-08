"""验证 participant 根 turn 的最终公开正文判定。"""

from trowel_py.discussion.turn_projection import FinalAnswerProjection


def test_work_after_intermediate_text_leaves_only_final_text() -> None:
    """工具前解释折叠进 Worked，工具后的连续总结才公开。"""

    projection = FinalAnswerProjection()
    projection.observe("text", "先检查一下。")
    projection.observe("tool_call")
    projection.observe("tool_result")
    projection.observe("text", "最终")
    projection.observe("usage_updated")
    projection.observe("text", "结论")
    projection.observe("finished")

    assert projection.answer() == "最终结论"


def test_pure_text_answer_keeps_all_chunks() -> None:
    """没有工作边界时，流式文本分片全部属于最终回答。"""

    projection = FinalAnswerProjection()
    projection.observe("text", "第一段")
    projection.observe("text", "第二段")

    assert projection.answer() == "第一段第二段"
