"""提供 Electron Host 启动和校验 Python sidecar 所需的后端边界。"""

from trowel_py.desktop.contract import (
    DESKTOP_CAPABILITIES,
    DESKTOP_PROTOCOL_VERSION,
    app_version,
)

__all__ = ["DESKTOP_CAPABILITIES", "DESKTOP_PROTOCOL_VERSION", "app_version"]
