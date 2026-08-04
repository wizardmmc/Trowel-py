"""创建只读取正式业务数据的桌面统计观察应用。"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from trowel_py.agent_host.binding import SessionBinding, binding_from_dict
from trowel_py.agent_host.store import BindingStore
from trowel_py.desktop.access import (
    DesktopCredentialMiddleware,
    validate_desktop_renderer_origin,
)
from trowel_py.desktop.contract import (
    DESKTOP_CAPABILITIES,
    DESKTOP_PROTOCOL_VERSION,
    app_version,
)
from trowel_py.statistics.agent.repository import FileAgentObservationReader
from trowel_py.statistics.calls.repository import CallStatisticsReader
from trowel_py.statistics.memory.repository import FileMemoryStatisticsReader
from trowel_py.statistics.routes import router as statistics_router
from trowel_py.statistics.runtime.repository import RuntimeStatisticsReader
from trowel_py.statistics.session_problems.repository import (
    FileSessionProblemStatisticsReader,
)
from trowel_py.telemetry.collector import CollectorSnapshot
from trowel_py.telemetry.storage import TelemetryDatabase

logger = logging.getLogger(__name__)


class ReadOnlyBindingStore(BindingStore):
    """不创建锁文件地读取正式 Agent binding 快照。"""

    def list_all(self) -> list[SessionBinding]:
        """读取当前绑定文件，并跳过无法转换的损坏记录。

        Returns:
            当前可解析的会话绑定；文件不存在或结构损坏时为空。
        """

        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records = document.get("sessions") if isinstance(document, dict) else None
        if not isinstance(records, dict):
            return []
        bindings: list[SessionBinding] = []
        for payload in records.values():
            if not isinstance(payload, dict):
                continue
            try:
                bindings.append(binding_from_dict(payload))
            except (KeyError, TypeError, ValueError):
                continue
        return bindings


class InspectionCollectorSnapshot:
    """声明观察进程没有自己的遥测写队列。"""

    def snapshot(self) -> CollectorSnapshot:
        """返回全零且不接收写入的 collector 状态。

        Returns:
            不冒充正式 App 实时 collector 的空进程快照。
        """

        return CollectorSnapshot(0, 0, 0, 0, 0, False, False, None)


def create_inspection_app() -> FastAPI:
    """创建只开放统计 GET 请求和桌面生命周期接口的 FastAPI 应用。

    Returns:
        直接读取正式数据库、不会启动 runtime 或后台任务的桌面应用。

    Raises:
        ValueError: Host 未提供绝对的正式数据读取目录。
    """

    read_data_dir = _read_data_directory(os.environ)
    app = FastAPI()
    desktop_credential = os.environ.pop("TROWEL_DESKTOP_CREDENTIAL", None)
    renderer_origin = os.environ.pop("TROWEL_DESKTOP_RENDERER_ORIGIN", None)
    app.state.desktop_instance_id = os.environ.pop("TROWEL_APP_INSTANCE_ID", "")
    app.state.read_data_dir = read_data_dir
    app.add_middleware(
        DesktopCredentialMiddleware,
        credential=desktop_credential,
    )

    from fastapi.middleware.cors import CORSMiddleware

    allowed_origins = ["http://localhost:5173", "http://localhost:5174"]
    if renderer_origin:
        allowed_origins.append(validate_desktop_renderer_origin(renderer_origin))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    _configure_statistics_readers(app, read_data_dir)
    app.include_router(statistics_router, prefix="/api/statistics")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        """返回只读观察 sidecar 的存活和模式。"""

        return {
            "success": True,
            "data": {"status": "ok", "mode": "read-only-inspection"},
            "error": None,
        }

    @app.get(
        "/api/desktop/readiness",
        include_in_schema=False,
        response_model=None,
    )
    def readiness() -> dict[str, object] | JSONResponse:
        """返回 Electron Host 完成版本与实例握手所需的事实。"""

        instance_id = str(app.state.desktop_instance_id).strip()
        if not instance_id:
            return _error(503, "desktop instance is not configured")
        return {
            "success": True,
            "data": {
                "status": "ready",
                "app_version": app_version(),
                "protocol_version": DESKTOP_PROTOCOL_VERSION,
                "instance_id": instance_id,
                "capabilities": list(DESKTOP_CAPABILITIES),
            },
            "error": None,
        }

    @app.get("/api/desktop/resources", include_in_schema=False)
    def resources() -> dict[str, object]:
        """声明观察 sidecar 没有启动可独立遗留的子资源。"""

        return {
            "success": True,
            "data": {"total": 0, "open": 0, "draining": False},
            "error": None,
        }

    @app.post("/api/desktop/drain", include_in_schema=False)
    def drain() -> dict[str, object]:
        """确认无后台资源需要收敛，随后由 Host 结束 sidecar。"""

        return {
            "success": True,
            "data": {"status": "closed", "remaining": 0},
            "error": None,
        }

    @app.post(
        "/api/telemetry/batches",
        status_code=202,
        include_in_schema=False,
    )
    def discard_host_telemetry() -> dict[str, object]:
        """确认收到 Host 观察事件，但不把它写入正式 telemetry.db。"""

        return {
            "success": True,
            "data": {
                "accepted": 0,
                "rejected": 0,
                "dropped": 0,
                "duplicate": 0,
                "error_categories": {},
            },
            "error": None,
        }

    @app.api_route(
        "/api/{path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    def reject_business_write(path: str) -> JSONResponse:
        """拒绝观察窗口发出的所有非桌面生命周期写请求。

        Args:
            path: 未被更具体路由匹配的 API 路径。
        """

        del path
        return _error(403, "read-only inspection mode")

    @app.exception_handler(Exception)
    def global_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """把只读查询异常转换成统一错误 envelope。"""

        logger.error(
            "Inspection query failed on %s %s: %s",
            request.method,
            request.url.path,
            exc,
        )
        return _error(500, str(exc))

    return app


def _configure_statistics_readers(app: FastAPI, read_data_dir: Path) -> None:
    """用显式正式数据根装配全部 Statistics 只读来源。

    Args:
        app: 要写入 reader 状态的观察应用。
        read_data_dir: 正式 Desktop 业务数据目录。
    """

    memory_root = read_data_dir / "memory"
    binding_store = ReadOnlyBindingStore(read_data_dir / "agent_sessions.json")
    app.state.agent_statistics_reader = FileAgentObservationReader(
        memory_root,
        binding_store,
    )
    app.state.memory_statistics_reader = FileMemoryStatisticsReader(
        memory_root,
        strict_read_only=True,
    )
    app.state.session_problem_statistics_reader = (
        FileSessionProblemStatisticsReader(memory_root)
    )
    app.state.telemetry_database = None
    app.state.telemetry_reader = None
    app.state.call_statistics_reader = None
    app.state.runtime_statistics_reader = None
    app.state.telemetry_collector = InspectionCollectorSnapshot()

    telemetry_path = read_data_dir / "telemetry.db"
    if not telemetry_path.is_file():
        return
    telemetry_database = TelemetryDatabase(telemetry_path, read_only=True)
    telemetry_reader = telemetry_database.reader()
    app.state.telemetry_database = telemetry_database
    app.state.telemetry_reader = telemetry_reader
    app.state.call_statistics_reader = CallStatisticsReader(telemetry_database)
    app.state.runtime_statistics_reader = RuntimeStatisticsReader(
        telemetry_reader,
        {
            "sessions.db": (memory_root / "meta" / "sessions.db", "memory.sessions"),
            "workspaces.db": (read_data_dir / "workspaces.db", "agent.workspaces"),
            "telemetry.db": (telemetry_path, "telemetry"),
        },
    )


def _read_data_directory(environment: Mapping[str, str]) -> Path:
    """校验并返回 Host 指定的正式数据读取根。

    Args:
        environment: 当前 sidecar 环境。

    Returns:
        展开后的绝对目录。

    Raises:
        ValueError: 环境变量缺失或目录不是绝对路径。
    """

    raw = environment.get("TROWEL_DESKTOP_READ_DATA_DIR", "").strip()
    if not raw:
        raise ValueError("TROWEL_DESKTOP_READ_DATA_DIR is required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError("TROWEL_DESKTOP_READ_DATA_DIR must be absolute")
    return path


def _error(status_code: int, message: str) -> JSONResponse:
    """构造观察应用共用的错误 envelope。

    Args:
        status_code: HTTP 状态码。
        message: 不含正文的稳定错误文本。

    Returns:
        与正式 API 一致的错误响应。
    """

    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": message},
    )
