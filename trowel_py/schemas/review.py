"""保留复习模型及其状态代码和评分类型的旧导入路径。

``FSRSState`` 和 ``ReviewLog`` 是模型，``FSRSStateCode`` 和
``ReviewRating`` 是 ``Literal`` 类型别名；它们均由
``trowel_py.review.models`` 定义。本模块继续支持
``trowel_py.schemas.review``。
"""

from trowel_py.review.models import (
    FSRSState,
    FSRSStateCode,
    ReviewLog,
    ReviewRating,
)

__all__ = ["FSRSState", "FSRSStateCode", "ReviewLog", "ReviewRating"]
