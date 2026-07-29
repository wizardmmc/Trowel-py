"""保留卡片提取结果模型的旧导入路径。

模型由 ``trowel_py.cards.schemas`` 定义；本模块继续支持
``trowel_py.schemas.extracted_card``。
"""

from trowel_py.cards.schemas import ExtractedCard, ExtractOutput

__all__ = ["ExtractedCard", "ExtractOutput"]
