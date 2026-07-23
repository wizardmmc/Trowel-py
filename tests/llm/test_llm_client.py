from unittest.mock import MagicMock, patch

import pytest

import json

from trowel_py.llm.filter import filter_secrets
from trowel_py.llm.client import LLMService, _extract_json
from trowel_py.schemas.extracted_card import ExtractOutput


def test_filter_secrets_redacts_aws_key():
    text = "my key is AKIAIOSFODNN7EXAMPLE please handle"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "AKIA" not in result


def test_filter_secrets_redacts_github_token():
    text = "token ghp_ABCdef123XYZ should be hidden"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "ghp_" not in result


def test_filter_secrets_redacts_openai_key():
    text = "api_key=sk-abc123DEF456ghi789 end"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "sk-" not in result


def test_filter_secrets_redacts_password():
    text = "password=S3cr3tP@ss here"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "S3cr3tP@ss" not in result


def test_filter_secrets_preserves_clean_text():
    text = "this is a normal diff with no secrets"
    assert filter_secrets(text) == text


def test_filter_secrets_redacts_multiple_values():
    user_prompt = "config has api_key=sk-Abc123XYZ and password=hunter2 inside"
    filtered = filter_secrets(user_prompt)
    assert "sk-Abc123XYZ" not in filtered
    assert "hunter2" not in filtered
    assert filtered.count("[REDACTED]") == 2


def test_structured_call_validates_provider_json():
    fake_provider = MagicMock()
    fake_provider.complete.return_value = json.dumps(
        {
            "cards": [
                {
                    "title": "WAL mode",
                    "category": "SQLite",
                    "explanation": "Write-Ahead Logging allows concurrent reads during writes",
                    "tags": ["database", "concurrency"],
                    "source_type": "git_diff",
                    "confidence": 4,
                    "difficulty": 3,
                }
            ]
        }
    )

    service = LLMService(fake_provider)
    user_prompt = "added WAL pragma to sqlite connection"
    result = service.structured_call(user_prompt, ExtractOutput)

    assert isinstance(result, ExtractOutput)
    assert len(result.cards) == 1
    assert result.cards[0].title == "WAL mode"


def test_structured_call_retries_on_failure():
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = [
        RuntimeError("API error"),
        RuntimeError("API error"),
        '{"cards": []}',
    ]

    service = LLMService(fake_provider)
    user_prompt = "some diff content"
    # 前两次失败会触发 1+2 秒退避，替换 sleep 以免测试真实等待。
    with patch("trowel_py.llm.client.time.sleep"):
        result = service.structured_call(user_prompt, ExtractOutput)

    assert isinstance(result, ExtractOutput)
    assert fake_provider.complete.call_count == 3


def test_cost_report_groups_calls_by_type():
    fake_provider = MagicMock()
    fake_provider.complete.return_value = '{"cards": []}'
    service = LLMService(fake_provider)

    service.structured_call("diff1", ExtractOutput, call_type="extract")
    service.structured_call("diff2", ExtractOutput, call_type="feynman-eval")

    report = service.get_cost_report()
    assert report.by_type["extract"]["calls"] == 1
    assert report.by_type["feynman-eval"]["calls"] == 1


def test_structured_call_raises_after_all_retries():
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = RuntimeError("API down")

    service = LLMService(fake_provider)
    with patch("trowel_py.llm.client.time.sleep"):
        with pytest.raises(RuntimeError):
            service.structured_call("diff", ExtractOutput)

    assert fake_provider.complete.call_count == 3


def test_retry_uses_exponential_backoff():
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = RuntimeError("API down")

    service = LLMService(fake_provider)
    with patch("trowel_py.llm.client.time.sleep") as mock_sleep:
        with pytest.raises(RuntimeError):
            service.structured_call("diff", ExtractOutput)

    sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
    assert sleep_args == [1, 2, 4]


def test_extract_json_keeps_plain_json():
    raw = '{"cards": [{"title": "x"}]}'
    assert _extract_json(raw) == raw


def test_extract_json_markdown_fence():
    raw = '```json\n{"cards": [{"title": "x"}]}\n```'
    assert _extract_json(raw) == '{"cards": [{"title": "x"}]}'


def test_extract_json_with_surrounding_prose():
    raw = '好的，提取结果如下：\n{"cards": []}\n希望帮到你'
    assert _extract_json(raw) == '{"cards": []}'


def test_extract_json_no_json_raises():
    with pytest.raises(ValueError):
        _extract_json("我不知道该提取什么")


def test_extract_json_empty_raises():
    with pytest.raises(ValueError):
        _extract_json("")
