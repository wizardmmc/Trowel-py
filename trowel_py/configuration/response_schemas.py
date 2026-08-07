"""冻结配置领域的脱敏 HTTP 成功与错误响应结构。"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

from trowel_py.configuration.models import (
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SecretKind,
    TaskId,
)

DataT = TypeVar("DataT")


class ErrorDetail(BaseModel):
    """返回不含请求原值的稳定领域错误。

    Attributes:
        code: 前端可分支处理的稳定错误码。
        message: 可直接展示的脱敏中文说明。
    """

    code: str
    message: str


class ErrorEnvelope(BaseModel):
    """返回配置接口统一的失败 envelope。

    Attributes:
        success: 固定为 False。
        data: 失败时固定为 None。
        error: 稳定错误码和脱敏说明。
    """

    success: Literal[False]
    data: None
    error: ErrorDetail


class ConfigurationEnvelope(BaseModel, Generic[DataT]):
    """返回配置接口统一的成功 envelope。

    Attributes:
        success: 固定为 True。
        data: 当前接口的结构化脱敏结果。
        error: 成功时固定为 None。
    """

    success: Literal[True]
    data: DataT
    error: None


class AuthResponse(BaseModel):
    """返回认证种类和配置状态，不含原值。

    Attributes:
        kind: API key 或 Codex 原生 OAuth。
        status: configured、missing 或 referenced。
    """

    kind: str
    status: str


class ProxyResponse(BaseModel):
    """返回连接代理的脱敏字段。

    Attributes:
        url: 不含 userinfo、query 和 fragment 的代理地址。
        username: 可读代理用户名。
        password_status: 代理密码是否已配置。
    """

    url: str | None
    username: str | None
    password_status: str


class ModelCatalogResponse(BaseModel):
    """返回当前请求身份下的上游模型列表。

    Attributes:
        status: idle、ready、error 或 stale。
        models: 去重后的真实 model ID。
        source_endpoint: 实际成功的脱敏端点。
        fetched_at: 成功获取的 UTC 时间。
        request_identity: 不含 secret 原值的请求身份哈希。
        error_code: 最近失败的稳定错误码。
    """

    status: str
    models: list[str]
    source_endpoint: str | None
    fetched_at: str | None
    request_identity: str | None
    error_code: str | None


class CodexCatalogEntryResponse(BaseModel):
    """返回 Codex 启动配置需要的一项模型元数据。

    Attributes:
        id: 上游真实 model ID。
        display_name: 可选展示名称。
        default_effort: 默认思考强度。
        supported_efforts: Codex 原生目录声明支持的思考强度。
    """

    id: str
    display_name: str | None
    default_effort: str | None
    supported_efforts: list[str]


class CodexOfficialAccountResponse(BaseModel):
    """返回 Official 账号槽位的脱敏原生状态。

    Attributes:
        status: logged_in、not_logged_in 或 unsupported。
        email: Codex 原生接口返回的账号邮箱。
        plan_type: plus、pro 等原生套餐标识。
        auth_mode: chatgpt 等原生认证模式。
        login_id: 当前 manager 最近一次设备登录尝试 ID。
        login_status: pending、completed 或 failed。
        login_error: 原生登录失败的脱敏说明。
    """

    status: str
    email: str | None
    plan_type: str | None
    auth_mode: str | None
    login_id: str | None = None
    login_status: str | None = None
    login_error: str | None = None


class CodexOfficialLoginResponse(BaseModel):
    """返回 Codex 原生 device-code 登录引导，不含任何 token。

    Attributes:
        login_id: 本次 app-server 登录尝试 ID。
        verification_url: 用户完成授权的 OpenAI 页面。
        user_code: 页面要求输入的一次性用户码。
    """

    login_id: str
    verification_url: str
    user_code: str


class ConnectionResponse(BaseModel):
    """返回连接的完整脱敏读模型。

    Attributes:
        id: Trowel 稳定本地 ID。
        version: 乐观并发版本。
        identity_version: runtime 启动身份版本。
        name: 用户设置的供应商名称。
        runtime: 消费连接的执行引擎。
        kind: 连接字段和认证种类。
        protocol: 上游请求协议。
        base_url: 不含 userinfo 的服务地址。
        models_url: 可选模型列表端点覆盖。
        upstream_host: 从服务地址解析的主机名。
        auth: 脱敏认证状态。
        login_directory: 保留兼容字段；Official 始终返回 None。
        login_directory_exists: Trowel 内部账号槽当前是否存在，不返回路径。
        claude_config_inherited: Claude 连接家是否完成过全局配置继承；其他连接为 None。
        codex_config_inherited: Codex 连接家是否完成过全局配置继承；其他连接为 None。
        proxy: 脱敏连接代理字段。
        claude_role_models: Claude 角色到 model ID 的映射。
        codex_catalog: Codex 模型和 effort 元数据。
        catalog: 当前模型列表状态。
        validation_status: 当前组合验证状态。
        capability_version: 只读能力表版本。
        last_session_choice: 最近一次成功会话的 model 和 effort。
        secret_versions: secret 用途到单调版本的映射。
        preview: 由保存事实生成的脱敏配置预览。
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
    auth: AuthResponse
    login_directory: str | None
    login_directory_exists: bool | None
    claude_config_inherited: bool | None
    codex_config_inherited: bool | None
    proxy: ProxyResponse
    claude_role_models: dict[str, str]
    codex_catalog: list[CodexCatalogEntryResponse]
    catalog: ModelCatalogResponse
    validation_status: str
    capability_version: str
    last_session_choice: dict[str, str | None] | None
    secret_versions: dict[SecretKind, int]
    preview: dict[str, Any]


class AgentConnectionModelResponse(BaseModel):
    """返回普通 Agent 可以选择的一项连接内模型。

    Attributes:
        id: Codex 上游真实模型 ID，或 Claude 主会话角色别名。
        display_name: 可选展示名称。
        available: 当前连接与 runtime 目录是否允许选择该模型。
        disabled_reason: 不可选时的稳定原因。
        efforts: runtime 原生目录声明支持的思考强度。
        default_effort: 连接 catalog 声明的默认思考强度。
    """

    id: str
    display_name: str | None
    available: bool
    disabled_reason: str | None
    efforts: list[str]
    default_effort: str | None


class AgentLastChoiceResponse(BaseModel):
    """返回连接最近一次真正建成原生会话的模型选择。

    Attributes:
        model: 最近成功使用的 Codex 模型 ID 或 Claude 角色别名。
        effort: 最近成功使用的思考强度。
    """

    model: str
    effort: str | None


class AgentConnectionOptionResponse(BaseModel):
    """返回 Agent 新建会话使用的一条完整脱敏连接选项。

    Attributes:
        id: 设置域稳定连接 ID。
        name: 用户设置的连接展示名。
        runtime: 消费该连接的原生 runtime。
        kind: 连接字段和认证种类。
        identity_version: runtime 身份变化时递增的版本。
        available: 连接是否至少有一个 runtime 可见模型可供创建会话。
        disabled_reason: 连接整体不可用时的稳定原因。
        last_session_choice: 最近一次原生会话成功后的模型选择。
        models: runtime 原生交互模型列表。
    """

    id: str
    name: str
    runtime: RuntimeKind
    kind: ConnectionKind
    identity_version: int
    available: bool
    disabled_reason: str | None
    last_session_choice: AgentLastChoiceResponse | None
    models: list[AgentConnectionModelResponse]


class FetchModelsResponse(BaseModel):
    """返回一次模型列表获取结果。

    Attributes:
        status: 成功时固定为 ready。
        models: 去重后的真实 model ID。
        source_endpoint: 实际成功的脱敏端点。
        fetched_at: 获取成功的 UTC 时间。
        request_identity: 本次请求身份哈希。
        connection_version: 保存结果后的连接版本。
        codex_catalog: Codex 原生顺序、默认 effort 和支持集合。
    """

    status: str
    models: list[str]
    source_endpoint: str | None
    fetched_at: str | None
    request_identity: str | None
    connection_version: int
    codex_catalog: list[CodexCatalogEntryResponse]


class CapabilityResponse(BaseModel):
    """返回会话配置的当前能力结论。

    Attributes:
        status: verified、unsupported、unknown 或 stale。
        version: 产生结论的能力表版本。
        source: 真实运行验证或未知结论的来源。
        eligible_tasks: 已验证可以绑定的后台任务。
    """

    status: str
    version: str
    source: str
    eligible_tasks: list[TaskId]


class SessionConfigurationResponse(BaseModel):
    """返回可复用会话配置及其实时可用性。

    Attributes:
        id: 稳定会话配置 ID。
        version: 乐观并发版本。
        name: 设置、Agent 和研讨共用的展示名称。
        runtime: 消费配置的执行方式。
        connection_id: 引用的连接 ID。
        connection_identity_version: 保存时冻结的连接身份版本。
        model: 上游真实 model ID。
        effort: 可选思考强度。
        capability: 当前能力结论。
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
    capability: CapabilityResponse
    availability: str
    disabled_reason: str | None


class TaskBindingResponse(BaseModel):
    """返回一项后台任务绑定。

    Attributes:
        task_id: 稳定后台任务 ID。
        version: 乐观并发版本。
        session_configuration_id: 当前引用的会话配置 ID；None 表示已解除绑定。
    """

    task_id: TaskId
    version: int
    session_configuration_id: str | None


class AgentDefaultsResponse(BaseModel):
    """返回之后新建 Agent 使用的默认条件。

    Attributes:
        version: 乐观并发版本；未保存时为 0。
        session_configuration_id: 可选默认会话配置。
        permission: 可选默认权限条件。
        memory_enabled: 是否默认注入 Memory。
        profile_enabled: 是否默认注入 Profile。
        self_enabled: 是否默认注入 Self。
    """

    version: int
    session_configuration_id: str | None
    permission: str | None
    memory_enabled: bool
    profile_enabled: bool
    self_enabled: bool


class ConfigurationCatalogResponse(BaseModel):
    """返回三类前端共用的配置事实集合。

    Attributes:
        connections: 全部未删除连接。
        session_configurations: 全部未删除会话配置。
        task_bindings: 后台任务的显式绑定。
        agent_defaults: 新建 Agent 默认条件。
    """

    connections: list[ConnectionResponse]
    session_configurations: list[SessionConfigurationResponse]
    task_bindings: list[TaskBindingResponse]
    agent_defaults: AgentDefaultsResponse


class SecretStatusResponse(BaseModel):
    """返回 secret 写入后的脱敏状态。

    Attributes:
        connection_id: 被更新的连接 ID。
        version: 更新后的连接版本。
        status: configured 或 missing。
    """

    connection_id: str
    version: int
    status: str


class PathEntryResponse(BaseModel):
    """返回一个解析路径的存在状态。

    Attributes:
        path: 当前模式使用的绝对或既有相对路径。
        exists: 路径当前是否存在。
        kind: file 或 directory。
    """

    path: str
    exists: bool
    kind: str


class PathStatusResponse(BaseModel):
    """返回当前数据模式和真实路径。

    Attributes:
        data_mode: packaged、canonical-dev、isolated-dev 或 browser。
        paths: 稳定路径名称到状态的映射。
    """

    data_mode: str
    paths: dict[str, PathEntryResponse]


class DiagnosticLayerResponse(BaseModel):
    """返回一层诊断的独立状态。

    Attributes:
        status: available、unavailable、unknown、unsupported 或 not_applicable。
        code: 失败、未知或暂不支持时的稳定原因。
    """

    status: str
    code: str | None


class ConnectionDiagnosticResponse(BaseModel):
    """返回一条连接的三层诊断。

    Attributes:
        connection_id: 被诊断连接的稳定 ID。
        connection_name: 当前展示名称。
        network: 模型服务网络层状态。
        runtime_launch: 本机 runtime 启动层状态。
        trowel_proxy: Trowel 兼容反代层状态。
    """

    connection_id: str
    connection_name: str
    network: DiagnosticLayerResponse
    runtime_launch: DiagnosticLayerResponse
    trowel_proxy: DiagnosticLayerResponse


class DiagnosticsResponse(BaseModel):
    """返回全部未删除连接的分层诊断。

    Attributes:
        connections: 每条连接互不冒充的网络、runtime 和反代状态。
    """

    connections: list[ConnectionDiagnosticResponse]
