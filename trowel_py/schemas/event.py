"""保留 ``EventLog`` 的旧导入路径。

模型由 ``trowel_py.events.models`` 定义；本模块继续支持
``trowel_py.schemas.event``。
"""

from trowel_py.events.models import EventLog

__all__ = ["EventLog"]
