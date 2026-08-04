"""定义配置领域可以安全返回给调用方的稳定错误。"""

from __future__ import annotations


class ConfigurationError(RuntimeError):
    """表示不包含凭据和第三方响应正文的配置失败。

    Attributes:
        code: 前后端共同判断失败类型的稳定机器码。
        message: 可以直接展示且不含 secret 的中文说明。
        status_code: HTTP 边界返回的状态码。
        commit_state: 是否应保留抛错前明确写入的诊断状态；默认回滚。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        commit_state: bool = False,
    ) -> None:
        """保存稳定错误码、安全说明、HTTP 状态和事务处理意图。"""

        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.commit_state = commit_state


def not_found(entity: str) -> ConfigurationError:
    """返回不泄漏目标内容的未找到错误。"""

    return ConfigurationError("NOT_FOUND", f"{entity}不存在", status_code=404)


def version_conflict() -> ConfigurationError:
    """返回乐观版本不再匹配的冲突错误。"""

    return ConfigurationError(
        "VERSION_CONFLICT",
        "配置已被其他操作更新，请重新加载后再保存",
        status_code=409,
    )
