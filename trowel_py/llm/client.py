from pydantic import BaseModel, Field

from typing import Literal, Protocol, TypeVar
from trowel_py.llm.filter import filter_secrets
from trowel_py.llm.prompts.registry import PROMPTS
from trowel_py.llm.types import CallType
import time
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class LLMConfig(BaseModel):
    provider: Literal["openai", "anthropic"]
    model: str = Field(min_length=2)
    api_key: str = Field(min_length=1)
    max_retries: int = Field(default=3)
    base_url: str = Field(default="http://localhost:1234/v1")


class CostEntry(BaseModel):
    call_type: CallType
    tokens_in: int
    tokens_out: int
    cost_used: float
    timestamp: str


class CostReport(BaseModel):
    total_cost: float
    by_type: dict[str, dict[str, float | int]]


class LLMProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


# SDK 在各自构造器内延迟导入，未选中的 provider 不成为启动时依赖。
class OpenAIProvider(LLMProvider):
    def __init__(self, config: LLMConfig):
        from openai import OpenAI

        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("OpenAI returned empty response")
        return content


class AnthropicProvider(LLMProvider):
    def __init__(self, config: LLMConfig):
        from anthropic import Anthropic

        self._client = Anthropic(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        for block in response.content:
            if block.type == "text":
                return block.text
        raise RuntimeError("No text block in Anthropic response")


def _call_with_retry(
    provider: LLMProvider, system_prompt: str, user_prompt: str, max_retries: int
) -> dict:
    last_error: Exception = RuntimeError("All retries exhausted.")
    for attempt in range(max_retries):
        try:
            raw = provider.complete(system_prompt, user_prompt)
            logger.info("LLM raw response (attempt %d): %s", attempt, raw)
            return json.loads(_extract_json(raw))
        except Exception as e:
            logger.warning("LLM call failed (attempt %d): %s", attempt, e)
            last_error = e
            wait = 2**attempt
            time.sleep(wait)
    raise last_error


def _extract_json(raw: str) -> str:
    """截取首个 `{` 到末个 `}`；缺少完整边界时抛出 `ValueError`。"""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"no JSON object in LLM response: {raw!r}")
    return raw[start : end + 1]


class LLMService:
    def __init__(self, provider: LLMProvider):
        self._provider = provider
        self._cost_log: list[CostEntry] = []

    def structured_call(
        self, user_prompt: str, schema: type[_ModelT], call_type: CallType = "extract"
    ) -> _ModelT:
        filtered_user_prompt = filter_secrets(user_prompt)
        response = _call_with_retry(
            self._provider, PROMPTS[call_type], filtered_user_prompt, 3
        )
        result = schema.model_validate(response)
        self._cost_log.append(
            CostEntry(
                call_type=call_type,
                tokens_in=0,
                tokens_out=0,
                cost_used=0.0,
                timestamp=datetime.now().isoformat(),
            )
        )
        MAX_COST_ENTRIES = 1000
        if len(self._cost_log) > MAX_COST_ENTRIES:
            self._cost_log.pop(0)
        return result

    def get_cost_report(self) -> CostReport:
        total = sum(e.cost_used for e in self._cost_log)
        by_type: dict[str, dict[str, float | int]] = {}
        for entry in self._cost_log:
            if entry.call_type not in by_type:
                by_type[entry.call_type] = {"calls": 0, "cost": 0.0}
            by_type[entry.call_type]["calls"] += 1
            by_type[entry.call_type]["cost"] += entry.cost_used
        return CostReport(total_cost=total, by_type=by_type)


def _provider_from_config(config: LLMConfig) -> LLMProvider:
    """根据配置创建唯一对应的 LLM provider。"""
    if config.provider == "openai":
        return OpenAIProvider(config)
    return AnthropicProvider(config)


def create_llm_service(config: LLMConfig) -> LLMService:
    """使用配置创建 LLM 服务。"""
    return LLMService(_provider_from_config(config))
