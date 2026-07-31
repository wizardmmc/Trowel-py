"""定义用户画像 HTTP 接口的请求和响应数据。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from trowel_py.profile.models import ProfileDimension, SuggestionStatus


class ProfileUpdate(BaseModel):
    """接收一份完整的用户画像更新。

    这是整份替换而非部分修改；更新时间由服务端生成，未传入的维度会按空字符串
    写入。

    Attributes:
        ability: 用户自述的背景、知识和能力。
        methodology: 用户长期采用的做事方式和流程偏好。
        expression: 用户希望 AI 采用的措辞、结构和讲解方式。
        goal: 需要持续影响后续协作方向的长期目标。
        other: 不属于上述四个维度、但会影响后续协作的稳定信息。
        source: 本次更新来自用户编辑还是 AI 校准，分别记录为
            ``user-edit`` 或 ``ai-calibration``。
    """

    ability: str = ""
    methodology: str = ""
    expression: str = ""
    goal: str = ""
    other: str = ""
    source: Literal["user-edit", "ai-calibration"] = "user-edit"


class ProfileDTO(BaseModel):
    """返回当前用户画像及最近一次更新时间和写入来源。

    Attributes:
        ability: 用户自述的背景、知识和能力。
        methodology: 用户长期采用的做事方式和流程偏好。
        expression: 用户希望 AI 采用的措辞、结构和讲解方式。
        goal: 需要持续影响后续协作方向的长期目标。
        other: 不属于上述四个维度、但会影响后续协作的稳定信息。
        updated: 画像最近一次写入的日期文本；尚未写入时为空字符串。
        source: 画像最后一次由哪种操作写入；接口写入时为 ``user-edit`` 或
            ``ai-calibration``，读取已有文件时其他非空文本也会原样返回。
    """

    ability: str
    methodology: str
    expression: str
    goal: str
    other: str
    updated: str
    source: str


class SuggestionDTO(BaseModel):
    """返回一条画像建议及其当前处理状态。

    Attributes:
        id: 更新建议状态时匹配的标识；队列中的重复 ID 会被一并更新。
        dimension: 建议要更新的画像维度。
        body: 供用户确认或编辑后追加到目标画像维度的文本。
        sources: 生成建议时记录的来源会话标识和证据文本。
        date: 建议生成日期文本。
        status: 建议当前处于待处理、已接受还是已丢弃状态。
    """

    id: str
    dimension: ProfileDimension
    body: str
    sources: list[str]
    date: str
    status: SuggestionStatus


class SuggestionStatusUpdate(BaseModel):
    """接收用户对一条画像建议的处理结果。

    该请求只修改建议队列中的状态，不会把建议正文写入用户画像。

    Attributes:
        status: ``accepted`` 将建议标记为已接受，``discarded`` 将建议标记为
            已丢弃。
    """

    status: Literal["accepted", "discarded"]
