"""保留花园接口模型的旧导入路径。

``PlantInfo`` 和 ``GardenStats`` 由 ``trowel_py.garden.schemas`` 定义。
``Card`` 由 ``trowel_py.cards.models`` 定义，并由
``trowel_py.garden.schemas`` 重新导出；本模块继续支持
``trowel_py.schemas.garden``。
"""

from trowel_py.garden.schemas import Card, GardenStats, PlantInfo

__all__ = ["Card", "GardenStats", "PlantInfo"]
