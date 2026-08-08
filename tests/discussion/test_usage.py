"""验证研讨 attempt 从双 runtime 真实事件形状归一化 token 用量。"""

from __future__ import annotations

from trowel_py.agent_host.binding import Runtime
from trowel_py.discussion.usage import AttemptUsageAccumulator


def test_claude_usage_deduplicates_repeated_message_snapshots() -> None:
    """Claude 同一 message 的多次快照只能累计一次。"""

    accumulator = AttemptUsageAccumulator(Runtime.CLAUDE_CODE)
    payload = {
        "message_id": "msg_202608080808551ec7ee11b9514b98",
        "model": "glm-5.2",
        "usage": {
            "input_tokens": 11086,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 23104,
            "output_tokens": 3186,
        },
    }

    accumulator.observe("context_usage", payload)
    accumulator.observe("context_usage", payload)

    assert accumulator.summary() == {
        "input_tokens": 11086,
        "output_tokens": 3186,
        "cache_read_input_tokens": 23104,
        "cache_creation_input_tokens": 0,
        "total_tokens": 37376,
    }


def test_codex_usage_sums_turn_deltas_from_cumulative_session_watermarks() -> None:
    """Codex 多次模型调用按累计水位差计整轮，不能只取最后一次调用。"""

    accumulator = AttemptUsageAccumulator(Runtime.CODEX)
    samples = (
        {
            "total": {
                "totalTokens": 18456,
                "inputTokens": 17672,
                "cachedInputTokens": 14144,
                "outputTokens": 784,
                "reasoningOutputTokens": 512,
            },
            "last": {
                "totalTokens": 18456,
                "inputTokens": 17672,
                "cachedInputTokens": 14144,
                "outputTokens": 784,
                "reasoningOutputTokens": 512,
            },
            "model_context_window": 258400,
        },
        {
            "total": {
                "totalTokens": 36110,
                "inputTokens": 34520,
                "cachedInputTokens": 27776,
                "outputTokens": 1590,
                "reasoningOutputTokens": 1024,
            },
            "last": {
                "totalTokens": 17654,
                "inputTokens": 16848,
                "cachedInputTokens": 13632,
                "outputTokens": 806,
                "reasoningOutputTokens": 512,
            },
            "model_context_window": 258400,
        },
        {
            "total": {
                "totalTokens": 52817,
                "inputTokens": 50311,
                "cachedInputTokens": 40192,
                "outputTokens": 2506,
                "reasoningOutputTokens": 1536,
            },
            "last": {
                "totalTokens": 16707,
                "inputTokens": 15791,
                "cachedInputTokens": 12416,
                "outputTokens": 916,
                "reasoningOutputTokens": 512,
            },
            "model_context_window": 258400,
        },
    )
    for payload in samples:
        accumulator.observe("usage_updated", payload)

    assert accumulator.summary() == {
        "input_tokens": 50311,
        "output_tokens": 2506,
        "cache_read_input_tokens": 40192,
        "reasoning_output_tokens": 1536,
        "total_tokens": 52817,
    }


def test_codex_new_attempt_uses_last_sample_as_first_delta() -> None:
    """复用同一 Codex thread 的下一轮只统计本 attempt 新增用量。"""

    accumulator = AttemptUsageAccumulator(Runtime.CODEX)
    accumulator.observe(
        "usage_updated",
        {
            "total": {
                "totalTokens": 71273,
                "inputTokens": 68122,
                "cachedInputTokens": 54144,
                "outputTokens": 3151,
                "reasoningOutputTokens": 2048,
            },
            "last": {
                "totalTokens": 18456,
                "inputTokens": 17811,
                "cachedInputTokens": 13952,
                "outputTokens": 645,
                "reasoningOutputTokens": 512,
            },
        },
    )
    accumulator.observe(
        "usage_updated",
        {
            "total": {
                "totalTokens": 90936,
                "inputTokens": 86904,
                "cachedInputTokens": 68864,
                "outputTokens": 4032,
                "reasoningOutputTokens": 2560,
            },
            "last": {
                "totalTokens": 19663,
                "inputTokens": 18782,
                "cachedInputTokens": 14720,
                "outputTokens": 881,
                "reasoningOutputTokens": 512,
            },
        },
    )

    assert accumulator.summary() == {
        "input_tokens": 36593,
        "output_tokens": 1526,
        "cache_read_input_tokens": 28672,
        "reasoning_output_tokens": 1024,
        "total_tokens": 38119,
    }
