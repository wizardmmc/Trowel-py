"""保留费曼提问与评估模型的旧导入路径。

模型由 ``trowel_py.feynman.schemas`` 定义；本模块继续支持
``trowel_py.schemas.feynman``。
"""

from trowel_py.feynman.schemas import FeynmanEvaluationSchema, FeynmanQuestionSchema

__all__ = ["FeynmanEvaluationSchema", "FeynmanQuestionSchema"]
