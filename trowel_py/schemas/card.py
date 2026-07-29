"""保留 ``Card`` 的旧导入路径。

模型由 ``trowel_py.cards.models`` 定义；本模块继续支持
``trowel_py.schemas.card``。
"""

from trowel_py.cards.models import Card

__all__ = ["Card"]
