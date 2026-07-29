"""保留卡片追问消息模型的旧导入路径。

模型由 ``trowel_py.cards.schemas`` 定义；本模块继续支持
``trowel_py.schemas.follow_up``。
"""

from trowel_py.cards.schemas import FollowUpMessageSchema

__all__ = ["FollowUpMessageSchema"]
