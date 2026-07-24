"""兼容旧 pet schema import；定义归 pet 所有。"""

from trowel_py.pet.models import Pet
from trowel_py.pet.schemas import EquipRequest, FeedRequest

__all__ = ["EquipRequest", "FeedRequest", "Pet"]
