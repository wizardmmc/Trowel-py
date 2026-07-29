"""封装 OpenAI 与 Anthropic 模型调用、重试、回复校验和占位成本统计。"""

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
    """保存模型供应商、模型名称和连接设置。

    Attributes:
        provider: 选择 OpenAI 或 Anthropic 调用实现。
        model: 传给所选供应商的模型名称。
        api_key: 创建供应商客户端时使用的 API 密钥。
        max_retries: 预留的重试配置，当前不生效；``LLMService`` 固定总共
            尝试三次。
        base_url: 模型 API 的根地址；默认指向本机 OpenAI 兼容服务。
    """

    provider: Literal["openai", "anthropic"]
    model: str = Field(min_length=2)
    api_key: str = Field(min_length=1)
    max_retries: int = Field(default=3)
    base_url: str = Field(default="http://localhost:1234/v1")


class CostEntry(BaseModel):
    """记录一次通过结构校验的模型调用。

    Attributes:
        call_type: 本次调用使用的系统提示词类型。
        tokens_in: 输入 token 数的占位字段；当前固定为 0，不代表真实用量。
        tokens_out: 输出 token 数的占位字段；当前固定为 0，不代表真实用量。
        cost_used: 调用成本的占位字段；当前固定为 0.0，不代表实际费用。
        timestamp: 结构校验完成时的本地 ISO 格式时间。
    """

    call_type: CallType
    tokens_in: int
    tokens_out: int
    cost_used: float
    timestamp: str


class CostReport(BaseModel):
    """汇总一个 ``LLMService`` 实例保留的调用记录。

    Attributes:
        total_cost: 所有记录中 ``cost_used`` 占位值的总和。
        by_type: 按调用类型分组的 ``calls`` 次数和 ``cost`` 占位值之和。
    """

    total_cost: float
    by_type: dict[str, dict[str, float | int]]


class LLMProvider(Protocol):
    """定义上层生成文本所需的模型调用接口。"""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """发送系统提示词和用户提示词并返回模型文本。

        Args:
            system_prompt: 约束模型角色和输出方式的系统提示词。
            user_prompt: 作为 ``user`` 消息发送的任务提示词。

        Returns:
            供应商返回的文本内容。
        """
        ...


# 两种 SDK 均在各自构造器内延迟导入，未选中的供应商不成为启动依赖。
class OpenAIProvider(LLMProvider):
    """通过 OpenAI SDK 的聊天补全接口生成文本。"""

    def __init__(self, config: LLMConfig):
        """创建 OpenAI 客户端并保存后续调用使用的模型名称。

        Args:
            config: 提供 API 密钥、根地址和模型名称的连接配置。
        """
        from openai import OpenAI

        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用聊天补全接口并返回第一条回复的文本。

        Args:
            system_prompt: 作为 ``system`` 消息发送的系统提示词。
            user_prompt: 作为 ``user`` 消息发送的用户提示词。

        Returns:
            第一条候选回复的文本。

        Raises:
            RuntimeError: 第一条候选回复没有文本。
        """
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
    """通过 Anthropic SDK 的消息接口生成文本。"""

    def __init__(self, config: LLMConfig):
        """创建 Anthropic 客户端并保存后续调用使用的模型名称。

        Args:
            config: 提供 API 密钥、根地址和模型名称的连接配置。
        """
        from anthropic import Anthropic

        self._client = Anthropic(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用消息接口并返回回复中的首个文本块。

        单次回复最多生成 4096 个 token。

        Args:
            system_prompt: 传给 Anthropic ``system`` 参数的系统提示词。
            user_prompt: 作为 ``user`` 消息发送的用户提示词。

        Returns:
            回复中第一个文本块的内容。

        Raises:
            RuntimeError: 回复中没有文本块。
        """
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
    """重试模型调用，并将回复中的 JSON 对象解析为字典。

    每次失败后按 1、2、4……秒退避，最后一次失败后也会等待；所有尝试失败时
    重新抛出最后一次模型调用或 JSON 解析异常。
    每次收到的模型原文会写入 INFO 日志，失败异常会写入 WARNING 日志。

    Args:
        provider: 实际发送提示词的模型调用实现。
        system_prompt: 每次尝试使用的系统提示词。
        user_prompt: 每次尝试使用的用户提示词。
        max_retries: 总尝试次数，而非首次调用后的额外重试次数。

    Returns:
        从模型回复解析出的 JSON 对象。
    """
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
    """截取模型回复中首个 ``{`` 到末个 ``}`` 的文本。

    本函数不校验 JSON 语法；找不到完整边界时抛出 ``ValueError``。
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end < start:
        raise ValueError(f"no JSON object in LLM response: {raw!r}")
    return raw[start : end + 1]


class LLMService:
    """遮盖用户提示词中符合常见格式的凭据，并校验模型的结构化回复。

    成功调用只在内存中保留最近 1000 条记录；当前记录中的 token 数和成本均为
    占位的零值，不代表供应商实际用量。
    """

    def __init__(self, provider: LLMProvider):
        """保存模型调用实现并建立空的内存调用记录。

        Args:
            provider: 接收提示词并返回文本的模型调用实现。
        """
        self._provider = provider
        self._cost_log: list[CostEntry] = []

    def structured_call(
        self, user_prompt: str, schema: type[_ModelT], call_type: CallType = "extract"
    ) -> _ModelT:
        """使用指定系统提示词生成并校验结构化模型回复。

        调用前会遮盖用户提示词中的常见凭据格式。模型调用或 JSON 解析固定总共
        尝试三次；三次均失败或数据模型校验失败时，最后的异常会向上传播。只有
        校验成功的调用才会写入占位成本记录。

        Args:
            user_prompt: 需要交给模型处理的业务输入。
            schema: 用于校验结构化回复的 Pydantic 模型类。
            call_type: ``PROMPTS`` 中已登记的系统提示词类型。

        Returns:
            由结构化回复校验得到的数据模型实例。

        Raises:
            KeyError: ``call_type`` 没有登记对应的系统提示词。
        """
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
        """汇总当前服务实例保留的调用次数和占位成本。"""
        total = sum(e.cost_used for e in self._cost_log)
        by_type: dict[str, dict[str, float | int]] = {}
        for entry in self._cost_log:
            if entry.call_type not in by_type:
                by_type[entry.call_type] = {"calls": 0, "cost": 0.0}
            by_type[entry.call_type]["calls"] += 1
            by_type[entry.call_type]["cost"] += entry.cost_used
        return CostReport(total_cost=total, by_type=by_type)


def _provider_from_config(config: LLMConfig) -> LLMProvider:
    """创建配置所选供应商的模型调用实现。"""
    if config.provider == "openai":
        return OpenAIProvider(config)
    return AnthropicProvider(config)


def create_llm_service(config: LLMConfig) -> LLMService:
    """使用配置所选供应商创建结构化模型调用服务。"""
    return LLMService(_provider_from_config(config))
