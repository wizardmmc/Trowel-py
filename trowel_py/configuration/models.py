"""定义配置领域的稳定值对象和脱敏读模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class RuntimeKind(StrEnum):
    """标识由哪种执行引擎或后台调用方式消费连接。"""

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    DIRECT_API = "direct_api"


class ConnectionKind(StrEnum):
    """标识连接采用哪套字段和认证规则。"""

    CLAUDE_COMPATIBLE = "claude_compatible"
    CODEX_OFFICIAL = "codex_official"
    CODEX_CUSTOM = "codex_custom"
    DIRECT_API = "direct_api"


class ProtocolKind(StrEnum):
    """标识上游实际接受的模型请求协议。"""

    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_RESPONSES = "openai_responses"
    CODEX_OFFICIAL = "codex_official"


class SecretKind(StrEnum):
    """标识只写 secret 在连接中的用途。"""

    API_KEY = "api_key"
    PROXY_PASSWORD = "proxy_password"


class TaskId(StrEnum):
    """标识可以独立绑定模型会话配置的后台任务。"""

    MEMORY_REFINE = "memory_refine"
    PROFILE_DISTILL = "profile_distill"
    MEMORY_DAILY = "memory_daily"
    MEMORY_WEEKLY = "memory_weekly"
    MEMORY_MONTHLY = "memory_monthly"


CLAUDE_ROLE_NAMES = frozenset(
    {"default", "sonnet", "opus", "fable", "haiku", "subagent"}
)


@dataclass(frozen=True)
class CodexCatalogEntry:
    """保存 Codex 启动目录需要的一项模型说明。

    Attributes:
        id: 上游模型列表返回的真实 model ID。
        display_name: UI 展示名称；None 表示直接显示 ID。
        default_effort: 新会话默认使用的思考强度；None 表示由 runtime 决定。
        supported_efforts: Codex 原生目录声明的思考强度；空元组表示未单独声明。
    """

    id: str
    display_name: str | None = None
    default_effort: str | None = None
    supported_efforts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectionDraft:
    """接收一条 Trowel 连接的全部可编辑非 secret 字段。

    Attributes:
        name: 设置页和会话选择器展示的用户名称。
        runtime: 消费连接的 Claude Code、Codex 或 direct API。
        kind: 决定字段结构和认证规则的连接种类。
        protocol: 上游实际接受的协议。
        base_url: 自定义模型服务根地址；Codex official 为 None。
        models_url: 上游模型列表端点覆盖；None 表示从 base URL 推导。
        login_directory: Codex official 原生登录目录引用。
        proxy_url: 不含 userinfo 的连接级代理地址。
        proxy_username: 代理认证用户名；密码通过独立只写 secret 保存。
        claude_role_models: Claude 各角色映射到的上游 model ID。
        codex_catalog: Codex app-server 使用的模型目录元数据。
        catalog_request_identity: 本次模型映射引用的已获取列表身份；不编辑映射时为 None。
    """

    name: str
    runtime: RuntimeKind
    kind: ConnectionKind
    protocol: ProtocolKind
    base_url: str | None = None
    models_url: str | None = None
    login_directory: str | None = None
    proxy_url: str | None = None
    proxy_username: str | None = None
    claude_role_models: dict[str, str] = field(default_factory=dict)
    codex_catalog: tuple[CodexCatalogEntry, ...] = ()
    catalog_request_identity: str | None = None


@dataclass(frozen=True)
class SessionConfigurationDraft:
    """接收一项 runtime、连接、模型和 effort 的命名组合。

    Attributes:
        name: 设置页、Agent 和研讨共同展示的名称。
        connection_id: Trowel 连接的稳定本地 ID。
        model: 上游实际执行的 model ID。
        effort: runtime 使用的思考强度；None 表示使用能力表默认值。
    """

    name: str
    connection_id: str
    model: str
    effort: str | None = None


@dataclass(frozen=True)
class AuthView:
    """返回认证种类和是否已配置，不包含凭据原值。

    Attributes:
        kind: API key 或 Codex official OAuth 引用。
        status: 当前为 configured、missing 或 referenced。
    """

    kind: str
    status: str


@dataclass(frozen=True)
class CatalogView:
    """返回模型列表的有效性、来源和去重 model ID。

    Attributes:
        status: idle、ready、error 或 stale。
        models: 当前请求身份下可用的 model ID；非 ready 时为空。
        source_endpoint: 实际成功的脱敏模型列表端点。
        fetched_at: 获取成功的 UTC 时间；尚未成功时为 None。
        request_identity: 绑定 runtime、协议、地址和 secret version 的哈希。
        error_code: 最近一次获取失败的稳定错误码。
        connection_version: 保存本次结果后的连接版本。
    """

    status: str
    models: tuple[str, ...] = ()
    source_endpoint: str | None = None
    fetched_at: str | None = None
    request_identity: str | None = None
    error_code: str | None = None
    connection_version: int = 0


@dataclass(frozen=True)
class CapabilityView:
    """返回组合是否经过真实运行验证以及使用的能力表版本。

    Attributes:
        status: verified、unsupported、unknown 或 stale。
        version: 产生判断的只读 capability registry 版本。
        source: 判断来自哪次真实证据或原生能力。
        eligible_tasks: 可以绑定这项配置的后台任务。
    """

    status: str
    version: str
    source: str
    eligible_tasks: tuple[TaskId, ...] = ()


@dataclass(frozen=True)
class ConnectionView:
    """返回一条不含 secret 和 OAuth 正文的连接事实。

    Attributes:
        id: Trowel 分配且不随编辑变化的连接 ID。
        version: 每次持久更新都会递增的乐观并发版本。
        identity_version: runtime 启动身份变化时递增的版本。
        name: 用户设置的展示名称。
        runtime: 消费连接的执行引擎或 direct API。
        kind: 连接字段结构和认证规则。
        protocol: 上游模型请求协议。
        base_url: 已确认不含 userinfo 的模型服务地址。
        models_url: 已确认不含 userinfo 的模型列表端点覆盖。
        upstream_host: 从 base URL 提取的主机名。
        auth: 脱敏认证状态。
        login_directory: Codex official 登录目录引用。
        login_directory_exists: 登录目录当前是否存在。
        claude_config_inherited: Claude 连接家是否完成过一次全局配置继承；其他连接为 None。
        codex_config_inherited: Codex 连接家是否完成过一次全局配置继承；其他连接为 None。
        proxy_url: 不含 userinfo 的连接级代理地址。
        proxy_username: 代理用户名；密码不返回。
        proxy_password_status: 代理密码是否已经配置。
        claude_role_models: Claude 角色到当前模型列表 ID 的映射。
        codex_catalog: Codex 使用的结构化模型目录。
        catalog: 当前模型列表状态。
        validation_status: 当前连接是否已经通过组合校验。
        capability_version: 当前只读能力表版本。
        last_session_choice: 最近一次成功创建会话的 model 和 effort。
        secret_versions: secret 名称到版本的映射；只用于请求身份，不含原值。
        preview: 根据持久事实生成的脱敏配置预览。
    """

    id: str
    version: int
    identity_version: int
    name: str
    runtime: RuntimeKind
    kind: ConnectionKind
    protocol: ProtocolKind
    base_url: str | None
    models_url: str | None
    upstream_host: str | None
    auth: AuthView
    login_directory: str | None
    login_directory_exists: bool | None
    claude_config_inherited: bool | None
    codex_config_inherited: bool | None
    proxy_url: str | None
    proxy_username: str | None
    proxy_password_status: str
    claude_role_models: dict[str, str]
    codex_catalog: tuple[CodexCatalogEntry, ...]
    catalog: CatalogView
    validation_status: str
    capability_version: str
    last_session_choice: dict[str, str | None] | None
    secret_versions: dict[str, int]
    preview: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        """转换成仅含 JSON 基本类型的脱敏响应。"""

        return {
            "id": self.id,
            "version": self.version,
            "identity_version": self.identity_version,
            "name": self.name,
            "runtime": self.runtime.value,
            "kind": self.kind.value,
            "protocol": self.protocol.value,
            "base_url": self.base_url,
            "models_url": self.models_url,
            "upstream_host": self.upstream_host,
            "auth": {"kind": self.auth.kind, "status": self.auth.status},
            "login_directory": self.login_directory,
            "login_directory_exists": self.login_directory_exists,
            "claude_config_inherited": self.claude_config_inherited,
            "codex_config_inherited": self.codex_config_inherited,
            "proxy": {
                "url": self.proxy_url,
                "username": self.proxy_username,
                "password_status": self.proxy_password_status,
            },
            "claude_role_models": dict(self.claude_role_models),
            "codex_catalog": [
                {
                    "id": item.id,
                    "display_name": item.display_name,
                    "default_effort": item.default_effort,
                    "supported_efforts": list(item.supported_efforts),
                }
                for item in self.codex_catalog
            ],
            "catalog": {
                "status": self.catalog.status,
                "models": list(self.catalog.models),
                "source_endpoint": self.catalog.source_endpoint,
                "fetched_at": self.catalog.fetched_at,
                "request_identity": self.catalog.request_identity,
                "error_code": self.catalog.error_code,
            },
            "validation_status": self.validation_status,
            "capability_version": self.capability_version,
            "last_session_choice": self.last_session_choice,
            "secret_versions": dict(self.secret_versions),
            "preview": self.preview,
        }


@dataclass(frozen=True)
class SessionConfigurationView:
    """返回一项经过 capability 校验的会话配置。

    Attributes:
        id: 会话配置稳定 ID。
        version: 乐观并发版本。
        name: 三类前端共同展示的名称。
        runtime: 使用的 runtime 或 direct API。
        connection_id: 引用的 Trowel 连接 ID。
        connection_identity_version: 创建时冻结的连接启动身份版本。
        model: 上游 model ID。
        effort: 思考强度；None 表示 runtime 默认值。
        capability: 当前能力结论和任务资格。
        availability: available 或 stale。
        disabled_reason: 不可用时的稳定原因。
    """

    id: str
    version: int
    name: str
    runtime: RuntimeKind
    connection_id: str
    connection_identity_version: int
    model: str
    effort: str | None
    capability: CapabilityView
    availability: str
    disabled_reason: str | None

    def to_wire(self) -> dict[str, Any]:
        """转换成配置 catalog 使用的 JSON 字典。"""

        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "runtime": self.runtime.value,
            "connection_id": self.connection_id,
            "connection_identity_version": self.connection_identity_version,
            "model": self.model,
            "effort": self.effort,
            "capability": {
                "status": self.capability.status,
                "version": self.capability.version,
                "source": self.capability.source,
                "eligible_tasks": [task.value for task in self.capability.eligible_tasks],
            },
            "availability": self.availability,
            "disabled_reason": self.disabled_reason,
        }


@dataclass(frozen=True)
class TaskBindingView:
    """返回后台任务当前引用的会话配置和版本。

    Attributes:
        task_id: 稳定后台任务 ID。
        version: 绑定每次变化时递增的版本。
        session_configuration_id: 当前使用的会话配置 ID；None 表示已解除绑定。
    """

    task_id: TaskId
    version: int
    session_configuration_id: str | None


@dataclass(frozen=True)
class AgentDefaultsView:
    """返回之后新建 Agent 会话采用的默认条件。

    Attributes:
        version: 默认条件每次变化时递增的乐观并发版本；尚未保存时为 0。
        session_configuration_id: 默认会话配置；None 表示尚未选择。
        permission: 默认权限条件；None 表示由 runtime 决定。
        memory_enabled: 新会话是否默认注入 Memory。
        profile_enabled: 新会话是否默认注入 Profile。
        self_enabled: 新会话是否默认注入 Self。
    """

    version: int
    session_configuration_id: str | None
    permission: str | None
    memory_enabled: bool
    profile_enabled: bool
    self_enabled: bool

    def to_wire(self) -> dict[str, Any]:
        """转换成设置页和 Agent 共用的 JSON 字典。"""

        return {
            "version": self.version,
            "session_configuration_id": self.session_configuration_id,
            "permission": self.permission,
            "memory_enabled": self.memory_enabled,
            "profile_enabled": self.profile_enabled,
            "self_enabled": self.self_enabled,
        }


@dataclass(frozen=True)
class PathEntry:
    """返回一个由现有 resolver 解析的真实路径。

    Attributes:
        path: 当前模式实际使用的路径。
        exists: 路径当前是否存在。
        kind: path 指向文件还是目录。
    """

    path: Path
    exists: bool
    kind: str


@dataclass(frozen=True)
class PathStatus:
    """返回当前数据模式和所有设置页需要的真实路径。

    Attributes:
        data_mode: packaged、canonical-dev、isolated-dev 或 browser。
        paths: 稳定路径名称到解析结果的映射。
    """

    data_mode: str
    paths: dict[str, PathEntry]
