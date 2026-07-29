"""保留草稿重新解释请求与结果模型的旧导入路径。

模型由 ``trowel_py.cards.schemas`` 定义；本模块继续支持
``trowel_py.schemas.re_explain``。
"""

from trowel_py.cards.schemas import ReExplainRequest, ReExplainResultSchema

__all__ = ["ReExplainRequest", "ReExplainResultSchema"]
