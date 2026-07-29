"""保留玩家数据和购买请求模型的旧导入路径。

玩家、资料和库存模型由 ``trowel_py.player.models`` 定义，购买请求由
``trowel_py.player.schemas`` 定义；本模块继续支持 ``trowel_py.schemas.player``。
"""

from trowel_py.player.models import InventoryItem, Player, PlayerProfile
from trowel_py.player.schemas import BuyItemRequest

__all__ = ["BuyItemRequest", "InventoryItem", "Player", "PlayerProfile"]
