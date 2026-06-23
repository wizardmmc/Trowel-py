from unittest.mock import MagicMock, patch

import pytest

import json

from trowel_py.llm.filter import filter_secrets
from trowel_py.llm.client import LLMService
from trowel_py.schemas.extracted_card import ExtractOutput


# --- filter_secrets tests ---

def test_filter_secrets_redacts_aws_key():
    """AWS access key pattern should be replaced with [REDACTED]"""
    text = "my key is AKIAIOSFODNN7EXAMPLE please handle"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "AKIA" not in result


def test_filter_secrets_redacts_github_token():
    """GitHub token pattern should be replaced with [REDACTED]"""
    text = "token ghp_ABCdef123XYZ should be hidden"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "ghp_" not in result


def test_filter_secrets_redacts_openai_key():
    """OpenAI API key pattern should be replaced with [REDACTED]"""
    text = "api_key=sk-abc123DEF456ghi789 end"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "sk-" not in result


def test_filter_secrets_redacts_password():
    """Password assignment pattern should be replaced with [REDACTED]"""
    text = "password=S3cr3tP@ss here"
    result = filter_secrets(text)
    assert "[REDACTED]" in result
    assert "S3cr3tP@ss" not in result


def test_filter_secrets_preserves_clean_text():
    """Text without secrets should pass through unchanged"""
    text = "this is a normal diff with no secrets"
    assert filter_secrets(text) == text


def test_filtter_secrets():
    """Multiple secret patterns in one text should all be replaced"""
    user_prompt = "config has api_key=sk-Abc123XYZ and password=hunter2 inside"
    filtered = filter_secrets(user_prompt)
    # secrets should be replaced
    assert "sk-Abc123XYZ" not in filtered
    assert "hunter2" not in filtered
    # [REDACTED] should appear for each replaced secret
    assert filtered.count("[REDACTED]") == 2


# --- structured_call tests ---

def test_structured_call():
    """Mock LLM should return valid ExtractOutput after Pydantic validation"""
    fake_provider = MagicMock()
    fake_provider.complete.return_value = json.dumps({
        "cards": [{
            "title": "WAL mode",
            "category": "SQLite",
            "explanation": "Write-Ahead Logging allows concurrent reads during writes",
            "tags": ["database", "concurrency"],
            "source_type": "git_diff",
            "confidence": 4,
            "difficulty": 3,
        }]
    })

    service = LLMService(fake_provider)
    user_prompt = "added WAL pragma to sqlite connection"
    result = service.structured_call(user_prompt, ExtractOutput)

    assert isinstance(result, ExtractOutput)
    assert len(result.cards) == 1
    assert result.cards[0].title == "WAL mode"


def test_structured_call_retries_on_failure():
    """Should retry up to 3 times on failure and succeed on the last attempt"""
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = [
        RuntimeError("API error"),
        RuntimeError("API error"),
        '{"cards": []}',
    ]

    service = LLMService(fake_provider)
    user_prompt = "some diff content"
    # mock time.sleep 避免真实等待 1+2=3 秒拖慢测试（前两次失败触发 backoff）
    with patch("trowel_py.llm.client.time.sleep"):
        result = service.structured_call(user_prompt, ExtractOutput)

    assert isinstance(result, ExtractOutput)
    assert fake_provider.complete.call_count == 3


# --- cost tracking tests ---

def test_cost_tracking():
    """Cost report should group calls by type and count correctly"""
    fake_provider = MagicMock()
    fake_provider.complete.return_value = '{"cards": []}'
    service = LLMService(fake_provider)

    service.structured_call("diff1", ExtractOutput, call_type="extract")
    service.structured_call("diff2", ExtractOutput, call_type="feynman-eval")

    report = service.get_cost_report()
    assert report.by_type["extract"]["calls"] == 1
    assert report.by_type["feynman-eval"]["calls"] == 1


# --- retry exhausts & backoff tests (slice 017 补的 TEST-001 缺口) ---

def test_structured_call_raises_after_all_retries():
    """3 次都失败应该抛出异常（最后一次的实际错误），且确实调用了 3 次"""
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = RuntimeError("API down")

    service = LLMService(fake_provider)
    with patch("trowel_py.llm.client.time.sleep"):
        with pytest.raises(RuntimeError):
            service.structured_call("diff", ExtractOutput)

    assert fake_provider.complete.call_count == 3


def test_retry_uses_exponential_backoff():
    """失败重试间隔应是指数退避：2^0=1, 2^1=2, 2^2=4 秒（共 3 次 sleep）"""
    fake_provider = MagicMock()
    fake_provider.complete.side_effect = RuntimeError("API down")

    service = LLMService(fake_provider)
    with patch("trowel_py.llm.client.time.sleep") as mock_sleep:
        with pytest.raises(RuntimeError):
            service.structured_call("diff", ExtractOutput)

    sleep_args = [call.args[0] for call in mock_sleep.call_args_list]
    assert sleep_args == [1, 2, 4]
