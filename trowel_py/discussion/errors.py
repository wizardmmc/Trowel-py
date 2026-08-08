"""定义研讨服务能够稳定映射到 HTTP 的领域错误。"""

from __future__ import annotations


class DiscussionError(Exception):
    """保存脱敏错误码、用户可读说明和 HTTP 状态码。

    Attributes:
        code: 前端用于稳定分类的错误代码。
        message: 不包含研讨正文或连接凭据的用户可读说明。
        status_code: HTTP 响应状态码。
    """

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        """创建一个可安全穿过 API 边界的领域错误。

        Args:
            code: 稳定错误代码。
            message: 脱敏用户说明。
            status_code: 对应 HTTP 状态码。
        """

        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class DiscussionNotFoundError(DiscussionError):
    """表示研讨不存在或已经从普通查询中删除。"""

    def __init__(self) -> None:
        """创建固定的 404 错误。"""

        super().__init__("DISCUSSION_NOT_FOUND", "找不到这场研讨", status_code=404)


class DiscussionVersionConflictError(DiscussionError):
    """表示写命令使用了已经过期的研讨版本。"""

    def __init__(self, current_version: int) -> None:
        """创建不回显请求正文的版本冲突错误。

        Args:
            current_version: 数据库当前版本，供界面重新获取快照。
        """

        super().__init__(
            "DISCUSSION_VERSION_CONFLICT",
            f"研讨状态已经变化，请刷新后重试（当前版本 {current_version}）",
            status_code=409,
        )
        self.current_version = current_version


class DiscussionStateError(DiscussionError):
    """表示当前生命周期状态不允许执行所请求的命令。"""

    def __init__(self, message: str) -> None:
        """创建固定为 409 的状态冲突错误。

        Args:
            message: 不包含正文的具体状态说明。
        """

        super().__init__("DISCUSSION_STATE_CONFLICT", message, status_code=409)


class DiscussionCommandConflictError(DiscussionError):
    """表示同一命令 ID 被用于不同请求。"""

    def __init__(self) -> None:
        """创建固定的幂等身份冲突错误。"""

        super().__init__(
            "DISCUSSION_COMMAND_CONFLICT",
            "同一命令标识已经用于另一项操作",
            status_code=409,
        )


class DiscussionRuntimeError(DiscussionError):
    """表示 participant 原生运行工具无法完成协调器操作。"""

    def __init__(self, message: str = "研讨运行工具暂时不可用") -> None:
        """创建不暴露 runtime 原始异常的 503 错误。

        Args:
            message: 可安全显示的运行工具问题说明。
        """

        super().__init__("DISCUSSION_RUNTIME_UNAVAILABLE", message, status_code=503)
