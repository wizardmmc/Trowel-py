"""定义用户画像及画像建议的取值范围与冻结数据对象。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# 表示最后一次写入路径的性质，不是逐字段来源。
ProfileSource = Literal["user-edit", "ai-calibration"]
ProfileDimension = Literal["ability", "methodology", "expression", "goal", "other"]
SuggestionStatus = Literal["pending", "accepted", "discarded"]


@dataclass(frozen=True)
class Profile:
    """记录用户维护的画像。

    AI 只能通过建议队列提案，不能直接改写画像正文。

    Attributes:
        ability: 用户能力水平。
        methodology: 用户的方法论偏好。
        expression: 用户的表达风格偏好。
        goal: 用户的长期目标。
        other: 不属于前四个维度的画像内容。
        updated: 画像更新时间文本。
        source: 最后一次写入路径的性质，不表示各字段各自的来源；读取时先转为
            文本，假值回退为 ``user-edit``，其他值保留；写入时由调用参数
            覆盖并按 ``ProfileSource`` 校验。
    """

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    updated: str = ""
    source: str = "user-edit"


@dataclass(frozen=True)
class Suggestion:
    """记录一条 AI 画像建议。

    接受或丢弃后的记录仍留在队列中供审计。

    Attributes:
        id: 用于匹配状态更新的标识；队列允许重复，同 ID 记录会一并更新。
        dimension: 建议要更新的画像维度。
        body: 建议写入该维度的正文。
        sources: 支持建议的来源引用。
        date: 建议生成日期文本。
        status: 建议当前的待处理、接受或丢弃状态。
        policy_version: 生成建议所用的门禁策略版本；旧记录缺失该字段时按
            ``1`` 读取，但不原地回写。
    """

    id: str
    dimension: ProfileDimension
    body: str
    sources: tuple[str, ...] = ()
    date: str = ""
    status: SuggestionStatus = "pending"
    policy_version: int = 1
