"""保留宠物状态和操作请求模型的旧导入路径。

``Pet`` 由 ``trowel_py.pet.models`` 定义，喂食和装备请求由
``trowel_py.pet.schemas`` 定义；本模块继续支持 ``trowel_py.schemas.pet``。
"""

from trowel_py.pet.models import Pet
from trowel_py.pet.schemas import EquipRequest, FeedRequest

__all__ = ["EquipRequest", "FeedRequest", "Pet"]
