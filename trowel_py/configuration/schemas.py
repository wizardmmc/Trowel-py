"""定义配置 HTTP 接口的请求模型，不接收可读 secret 字段。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trowel_py.configuration.models import (
    CodexCatalogEntry,
    ConnectionDraft,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    SessionConfigurationDraft,
)


class CodexCatalogEntryRequest(BaseModel):
    """接收一项引用当前模型列表的 Codex catalog 元数据。

    Attributes:
        id: 当前模型列表中的 model ID。
        display_name: 设置页和 Agent 展示名称。
        default_effort: 新会话默认思考强度。
        supported_efforts: 该模型已经验证的思考强度。
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=512)
    display_name: str | None = Field(default=None, max_length=120)
    default_effort: str | None = Field(default=None, max_length=32)
    supported_efforts: list[str] = Field(default_factory=list, max_length=16)

    def to_domain(self) -> CodexCatalogEntry:
        """转换为不依赖 Pydantic 的领域值对象。"""

        return CodexCatalogEntry(
            id=self.id,
            display_name=self.display_name,
            default_effort=self.default_effort,
            supported_efforts=tuple(self.supported_efforts),
        )


class ConnectionRequest(BaseModel):
    """接收连接的全部非 secret 可编辑字段。

    Attributes:
        name: 设置页展示的供应商名称。
        runtime: 消费连接的 runtime 或 direct API。
        kind: 选择字段结构和认证规则的连接种类。
        protocol: 上游请求协议。
        base_url: 自定义连接的模型服务地址。
        models_url: 可选的模型列表端点覆盖。
        login_directory: 兼容旧请求的保留字段；Official 新建时必须为空。
        proxy_url: 不含 userinfo 的连接级代理地址。
        proxy_username: 连接级代理用户名。
        claude_role_models: Claude 角色到 model ID 的映射。
        codex_catalog: Codex app-server 使用的模型目录。
        catalog_request_identity: 模型映射引用的已获取列表身份。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    runtime: RuntimeKind
    kind: ConnectionKind
    protocol: ProtocolKind
    base_url: str | None = Field(default=None, max_length=2048)
    models_url: str | None = Field(default=None, max_length=2048)
    login_directory: str | None = Field(default=None, max_length=4096)
    proxy_url: str | None = Field(default=None, max_length=2048)
    proxy_username: str | None = Field(default=None, max_length=512)
    claude_role_models: dict[str, str] = Field(default_factory=dict, max_length=6)
    codex_catalog: list[CodexCatalogEntryRequest] = Field(
        default_factory=list, max_length=10_000
    )
    catalog_request_identity: str | None = None

    def to_domain(self) -> ConnectionDraft:
        """转换成 service 校验的完整连接草稿。"""

        return ConnectionDraft(
            name=self.name,
            runtime=self.runtime,
            kind=self.kind,
            protocol=self.protocol,
            base_url=self.base_url,
            models_url=self.models_url,
            login_directory=self.login_directory,
            proxy_url=self.proxy_url,
            proxy_username=self.proxy_username,
            claude_role_models=dict(self.claude_role_models),
            codex_catalog=tuple(item.to_domain() for item in self.codex_catalog),
            catalog_request_identity=self.catalog_request_identity,
        )


class UpdateConnectionRequest(ConnectionRequest):
    """接收完整连接替换及调用方看到的乐观版本。

    Attributes:
        expected_version: 编辑开始时读取的连接版本。
    """

    expected_version: int = Field(ge=1)


class FetchModelsRequest(BaseModel):
    """要求后端用已保存 secret 获取当前或草稿身份的模型列表。

    Attributes:
        expected_version: 发起请求时读取的连接版本。
        draft: 可选的未保存非 secret 字段；用于编辑地址后先验证再保存。
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    draft: ConnectionRequest | None = None


class CreateSessionConfigurationRequest(BaseModel):
    """接收一项命名会话配置及当前连接版本。

    Attributes:
        name: 设置、Agent 和研讨共同展示的名称。
        connection_id: 引用的 Trowel 连接 ID。
        model: 当前有效模型列表中的 model ID。
        effort: 已验证思考强度；None 表示 runtime 默认值。
        expected_connection_version: 创建前读取的连接版本。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    connection_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str | None = Field(default=None, max_length=32)
    expected_connection_version: int = Field(ge=1)

    def to_domain(self) -> SessionConfigurationDraft:
        """转换成 service 使用的会话配置草稿。"""

        return SessionConfigurationDraft(
            name=self.name,
            connection_id=self.connection_id,
            model=self.model,
            effort=self.effort,
        )


class UpdateSessionConfigurationRequest(CreateSessionConfigurationRequest):
    """接收完整会话配置替换及调用方看到的配置版本。

    Attributes:
        expected_version: 编辑开始时读取的会话配置版本。
    """

    expected_version: int = Field(ge=1)


class PutTaskBindingRequest(BaseModel):
    """接收后台任务绑定的目标配置和乐观版本。

    Attributes:
        session_configuration_id: 经过任务级 capability 校验的会话配置 ID。
        expected_version: 当前绑定版本；首次创建固定为 0。
    """

    model_config = ConfigDict(extra="forbid")

    session_configuration_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0)


class PutAgentDefaultsRequest(BaseModel):
    """接收只影响之后新建 Agent 会话的默认条件。

    Attributes:
        expected_version: 当前默认条件版本；首次保存固定为 0。
        session_configuration_id: 默认会话配置；None 表示未选择。
        permission: 默认权限条件；None 表示 runtime 默认值。
        memory_enabled: 是否默认注入 Memory。
        profile_enabled: 是否默认注入 Profile。
        self_enabled: 是否默认注入 Self。
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    session_configuration_id: str | None = None
    permission: str | None = Field(default=None, max_length=120)
    memory_enabled: bool = Field(strict=True)
    profile_enabled: bool = Field(strict=True)
    self_enabled: bool = Field(strict=True)
