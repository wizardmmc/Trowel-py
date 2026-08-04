"""提供连接、会话配置、任务绑定、路径和诊断的脱敏 HTTP API。"""

from __future__ import annotations

import inspect
import json
from collections.abc import Iterator
from functools import wraps
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from trowel_py.configuration.diagnostics import build_diagnostics
from trowel_py.configuration.errors import ConfigurationError
from trowel_py.configuration.migration import migrate_legacy_llm_config
from trowel_py.configuration.models import SecretKind, TaskId
from trowel_py.configuration.paths import build_path_status
from trowel_py.configuration.repository import ConfigurationRepository
from trowel_py.configuration.response_schemas import (
    AgentDefaultsResponse,
    ConfigurationCatalogResponse,
    ConfigurationEnvelope,
    ConnectionResponse,
    DiagnosticsResponse,
    ErrorEnvelope,
    FetchModelsResponse,
    PathStatusResponse,
    SecretStatusResponse,
    SessionConfigurationResponse,
    TaskBindingResponse,
)
from trowel_py.configuration.schemas import (
    ConnectionRequest,
    CreateSessionConfigurationRequest,
    FetchModelsRequest,
    PutAgentDefaultsRequest,
    PutTaskBindingRequest,
    UpdateConnectionRequest,
    UpdateSessionConfigurationRequest,
)
from trowel_py.configuration.service import ConfigurationService
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations
from trowel_py.memory.paths import find_config_path


class ConfigurationRoute(APIRoute):
    """把配置接口的框架校验错误压缩成不含请求原值的响应。"""

    def get_route_handler(self) -> Any:
        """包装 FastAPI handler，避免错误详情回显误填的 secret。"""

        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Any:
            """执行原 handler，并把校验失败转换为稳定脱敏错误。"""

            try:
                return await original_handler(request)
            except RequestValidationError:
                return _error(
                    ConfigurationError(
                        "INVALID_REQUEST",
                        "配置请求字段无效",
                        status_code=422,
                    )
                )
        return safe_handler


_ERROR_RESPONSES = {
    status_code: {"model": ErrorEnvelope}
    for status_code in (400, 401, 404, 409, 422, 502, 504)
}
_MAX_SECRET_BODY_BYTES = 65_536
_MAX_SECRET_VALUE_CHARS = 16_384

router = APIRouter(
    route_class=ConfigurationRoute,
    tags=["configuration"],
    responses=_ERROR_RESPONSES,
)


def get_configuration_service() -> Iterator[ConfigurationService]:
    """打开主库、应用 migration 并提供请求级事务边界。

    route 和集成测试必须通过 ``dependency_overrides`` 注入临时仓储；正式装配按
    当前应用数据根打开主库。旧配置导入失败只保留可见的未迁移状态，不阻断设置读取。
    """

    connection = create_db()
    try:
        run_migrations(connection)
        repository = ConfigurationRepository(connection)
        migrate_legacy_llm_config(repository, find_config_path())
        yield ConfigurationService(repository)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _success(data: Any) -> dict[str, Any]:
    """构造全局一致的成功 envelope。"""

    return {"success": True, "data": data, "error": None}


def _error(exc: ConfigurationError) -> JSONResponse:
    """把不含 secret 的领域错误转换成稳定 HTTP envelope。"""

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "data": None,
            "error": {"code": exc.code, "message": exc.message},
        },
    )


def _transactional(handler: Any) -> Any:
    """让领域错误在返回 HTTP 响应前先决定提交诊断或回滚业务写入。"""

    if inspect.iscoroutinefunction(handler):

        @wraps(handler)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            """包装异步配置端点。"""

            service: ConfigurationService = kwargs["service"]
            try:
                return await handler(*args, **kwargs)
            except ConfigurationError as exc:
                _finish_domain_error(service, exc)
                return _error(exc)

        return async_wrapper

    @wraps(handler)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        """包装同步配置端点。"""

        service: ConfigurationService = kwargs["service"]
        try:
            return handler(*args, **kwargs)
        except ConfigurationError as exc:
            _finish_domain_error(service, exc)
            return _error(exc)

    return sync_wrapper


def _finish_domain_error(
    service: ConfigurationService, exc: ConfigurationError
) -> None:
    """只保留明确标记的失败诊断，其他领域错误回滚当前请求写入。"""

    if exc.commit_state:
        service.repository.connection.commit()
    else:
        service.repository.connection.rollback()


@router.get(
    "/connections",
    response_model=ConfigurationEnvelope[list[ConnectionResponse]],
)
@_transactional
def list_connections(
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """返回全部未删除连接的脱敏状态。"""

    return _success([item.to_wire() for item in service.list_connections()])


@router.post(
    "/connections",
    status_code=201,
    response_model=ConfigurationEnvelope[ConnectionResponse],
)
@_transactional
def create_connection(
    request: ConnectionRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """创建一条不含 secret 的连接。"""

    return _success(service.create_connection(request.to_domain()).to_wire())


@router.get(
    "/connections/{connection_id}",
    response_model=ConfigurationEnvelope[ConnectionResponse],
)
@_transactional
def get_connection(
    connection_id: str,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """读取指定连接的脱敏状态和生成预览。"""

    return _success(service.get_connection(connection_id).to_wire())


@router.put(
    "/connections/{connection_id}",
    response_model=ConfigurationEnvelope[ConnectionResponse],
)
@_transactional
def update_connection(
    connection_id: str,
    request: UpdateConnectionRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """按乐观版本完整替换连接的非 secret 字段。"""

    return _success(
        service.update_connection(
            connection_id,
            expected_version=request.expected_version,
            draft=request.to_domain(),
        ).to_wire()
    )


@router.delete(
    "/connections/{connection_id}", response_model=ConfigurationEnvelope[None]
)
@_transactional
def delete_connection(
    connection_id: str,
    expected_version: int = Query(ge=1),
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """软删除连接并删除所有 secret。"""

    service.delete_connection(connection_id, expected_version=expected_version)
    return _success(None)


@router.put(
    "/connections/{connection_id}/secrets/{secret_kind}",
    response_model=ConfigurationEnvelope[SecretStatusResponse],
)
@_transactional
async def write_secret(
    connection_id: str,
    secret_kind: SecretKind,
    request: Request,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any] | JSONResponse:
    """写入或删除 secret；自行解析请求，确保校验错误不回显原值。"""

    command = await _read_secret_command(request)

    if not isinstance(command, dict) or set(command) not in (
        {"expected_version", "action", "value"},
        {"expected_version", "action"},
    ):
        return _error(
            ConfigurationError(
                "INVALID_SECRET_COMMAND",
                "secret 命令字段无效",
                status_code=422,
            )
        )
    expected_version = command.get("expected_version")
    action = command.get("action")
    if not isinstance(expected_version, int) or expected_version < 1:
        return _error(
            ConfigurationError(
                "INVALID_SECRET_COMMAND", "secret 版本无效", status_code=422
            )
        )
    if action == "set":
        value = command.get("value")
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > _MAX_SECRET_VALUE_CHARS
        ):
            return _error(
                ConfigurationError(
                    "INVALID_SECRET_COMMAND", "secret 新值无效", status_code=422
                )
            )
    elif action == "delete" and "value" not in command:
        value = None
    else:
        return _error(
            ConfigurationError(
                "INVALID_SECRET_COMMAND", "secret 动作无效", status_code=422
            )
        )
    updated = service.write_secret(
        connection_id,
        expected_version=expected_version,
        kind=secret_kind,
        value=value,
    )
    return _success(
        {
            "connection_id": updated.id,
            "version": updated.version,
            "status": (
                updated.auth.status
                if secret_kind == SecretKind.API_KEY
                else updated.proxy_password_status
            ),
        }
    )


async def _read_secret_command(request: Request) -> dict[str, Any] | None:
    """有界读取 secret 命令，并在解析失败时丢弃全部原始输入。"""

    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_SECRET_BODY_BYTES:
                return None
            body.extend(chunk)
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


@router.post(
    "/connections/{connection_id}/models:fetch",
    response_model=ConfigurationEnvelope[FetchModelsResponse],
)
@_transactional
async def fetch_models(
    connection_id: str,
    request: FetchModelsRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """用后端保存的 secret 获取当前或草稿身份的模型列表。"""

    result = await service.fetch_models(
        connection_id,
        expected_version=request.expected_version,
        draft=request.draft.to_domain() if request.draft is not None else None,
    )
    return _success(
        {
            "status": result.status,
            "models": list(result.models),
            "source_endpoint": result.source_endpoint,
            "fetched_at": result.fetched_at,
            "request_identity": result.request_identity,
            "connection_version": result.connection_version,
        }
    )


@router.get(
    "/session-configurations",
    response_model=ConfigurationEnvelope[list[SessionConfigurationResponse]],
)
@_transactional
def list_session_configurations(
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """返回设置、Agent 和研讨共用的会话配置 catalog。"""

    return _success(
        [item.to_wire() for item in service.list_session_configurations()]
    )


@router.post(
    "/session-configurations",
    status_code=201,
    response_model=ConfigurationEnvelope[SessionConfigurationResponse],
)
@_transactional
def create_session_configuration(
    request: CreateSessionConfigurationRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """创建一项经过当前 catalog 和 capability 校验的会话配置。"""

    item = service.create_session_configuration(
        request.to_domain(),
        expected_connection_version=request.expected_connection_version,
    )
    return _success(item.to_wire())


@router.get(
    "/session-configurations/{configuration_id}",
    response_model=ConfigurationEnvelope[SessionConfigurationResponse],
)
@_transactional
def get_session_configuration(
    configuration_id: str,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """读取一项会话配置及其实时可用性。"""

    return _success(service.get_session_configuration(configuration_id).to_wire())


@router.put(
    "/session-configurations/{configuration_id}",
    response_model=ConfigurationEnvelope[SessionConfigurationResponse],
)
@_transactional
def update_session_configuration(
    configuration_id: str,
    request: UpdateSessionConfigurationRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """重新校验并完整替换一项会话配置。"""

    item = service.update_session_configuration(
        configuration_id,
        expected_version=request.expected_version,
        expected_connection_version=request.expected_connection_version,
        draft=request.to_domain(),
    )
    return _success(item.to_wire())


@router.delete(
    "/session-configurations/{configuration_id}",
    response_model=ConfigurationEnvelope[None],
)
@_transactional
def delete_session_configuration(
    configuration_id: str,
    expected_version: int = Query(ge=1),
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """软删除一项会话配置。"""

    service.delete_session_configuration(
        configuration_id, expected_version=expected_version
    )
    return _success(None)


@router.get(
    "/task-bindings",
    response_model=ConfigurationEnvelope[list[TaskBindingResponse]],
)
@_transactional
def list_task_bindings(
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """返回 Memory/Profile 各后台任务的独立绑定。"""

    return _success(
        [
            {
                "task_id": item.task_id.value,
                "version": item.version,
                "session_configuration_id": item.session_configuration_id,
            }
            for item in service.list_task_bindings()
        ]
    )


@router.put(
    "/task-bindings/{task_id}",
    response_model=ConfigurationEnvelope[TaskBindingResponse],
)
@_transactional
def put_task_binding(
    task_id: TaskId,
    request: PutTaskBindingRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """按任务级 capability 保存一项后台绑定。"""

    item = service.put_task_binding(
        task_id,
        session_configuration_id=request.session_configuration_id,
        expected_version=request.expected_version,
    )
    return _success(
        {
            "task_id": item.task_id.value,
            "version": item.version,
            "session_configuration_id": item.session_configuration_id,
        }
    )


@router.delete(
    "/task-bindings/{task_id}",
    response_model=ConfigurationEnvelope[TaskBindingResponse],
)
@_transactional
def delete_task_binding(
    task_id: TaskId,
    expected_version: int = Query(ge=1),
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """删除一项后台任务绑定。"""

    item = service.delete_task_binding(task_id, expected_version=expected_version)
    return _success(
        {
            "task_id": item.task_id.value,
            "version": item.version,
            "session_configuration_id": item.session_configuration_id,
        }
    )


@router.get(
    "/agent-defaults",
    response_model=ConfigurationEnvelope[AgentDefaultsResponse],
)
@_transactional
def get_agent_defaults(
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """返回只影响之后新建会话的 Agent 默认条件。"""

    return _success(service.get_agent_defaults().to_wire())


@router.put(
    "/agent-defaults",
    response_model=ConfigurationEnvelope[AgentDefaultsResponse],
)
@_transactional
def put_agent_defaults(
    request: PutAgentDefaultsRequest,
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """按乐观版本保存 Agent 默认条件。"""

    item = service.put_agent_defaults(
        expected_version=request.expected_version,
        session_configuration_id=request.session_configuration_id,
        permission=request.permission,
        memory_enabled=request.memory_enabled,
        profile_enabled=request.profile_enabled,
        self_enabled=request.self_enabled,
    )
    return _success(item.to_wire())


@router.delete(
    "/agent-defaults",
    response_model=ConfigurationEnvelope[AgentDefaultsResponse],
)
@_transactional
def delete_agent_defaults(
    expected_version: int = Query(ge=1),
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """删除显式 Agent 默认条件并恢复兼容默认值。"""

    return _success(
        service.delete_agent_defaults(expected_version=expected_version).to_wire()
    )


@router.get(
    "/catalog",
    response_model=ConfigurationEnvelope[ConfigurationCatalogResponse],
)
@_transactional
def get_configuration_catalog(
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """返回三类前端共用的连接、会话配置、绑定和 Agent 默认事实。"""

    return _success(
        {
            "connections": [item.to_wire() for item in service.list_connections()],
            "session_configurations": [
                item.to_wire() for item in service.list_session_configurations()
            ],
            "task_bindings": [
                {
                    "task_id": item.task_id.value,
                    "version": item.version,
                    "session_configuration_id": item.session_configuration_id,
                }
                for item in service.list_task_bindings()
            ],
            "agent_defaults": service.get_agent_defaults().to_wire(),
        }
    )


@router.get("/paths", response_model=ConfigurationEnvelope[PathStatusResponse])
def get_paths() -> dict[str, Any]:
    """返回当前数据模式及现有 resolver 解析的真实路径。"""

    status = build_path_status()
    return _success(
        {
            "data_mode": status.data_mode,
            "paths": {
                name: {
                    "path": str(entry.path),
                    "exists": entry.exists,
                    "kind": entry.kind,
                }
                for name, entry in status.paths.items()
            },
        }
    )


@router.get(
    "/diagnostics", response_model=ConfigurationEnvelope[DiagnosticsResponse]
)
@_transactional
def get_diagnostics(
    service: ConfigurationService = Depends(get_configuration_service),
) -> dict[str, Any]:
    """分层返回连接网络、runtime 启动和 Trowel 反代状态。"""

    return _success(build_diagnostics(service.list_connections()))
