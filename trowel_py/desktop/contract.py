"""定义 Desktop Host 与 Python sidecar 共同校验的版本契约。"""

from importlib.metadata import PackageNotFoundError, version

DESKTOP_PROTOCOL_VERSION = 1
DESKTOP_CAPABILITIES = ("agent", "memory", "review")


def app_version() -> str:
    """返回当前 Python sidecar 所属的 Trowel 应用版本。"""
    try:
        return version("trowel-py")
    except PackageNotFoundError:
        # 源码目录尚未安装时仍保持与 pyproject 的当前开发版本一致。
        return "0.2.0"
