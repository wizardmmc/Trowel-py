"""创建 FastAPI 应用，并管理 Agent、Memory 和后台任务的生命周期。"""

import logging
import os
import uuid
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from trowel_py.agent_host.routes import router as agent_router
from trowel_py.agent_host.runtime_availability import detect_runtime_availability
from trowel_py.agent_host.workspaces import (
    RecentWorkspaceStore,
    resolve_recent_workspaces_path,
)
from trowel_py.quota.routes import router as quota_router
from trowel_py.cards.routes import router as card_router
from trowel_py.cc_host.proxy import (
    TUI_SYSTEM_IDENTITY,
    load_settings_env,
    router as proxy_router,
)
from trowel_py.cc_host.routes import router as cc_host_router
from trowel_py.desktop.access import (
    DesktopCredentialMiddleware,
    validate_desktop_renderer_origin,
)
from trowel_py.desktop.routes import router as desktop_router
from trowel_py.events.routes import router as events_router
from trowel_py.feynman.routes import router as feynman_router
from trowel_py.garden.routes import router as garden_router
from trowel_py.pet.routes import router as pet_router
from trowel_py.player.routes import router as player_router
from trowel_py.profile.routes import router as profile_router
from trowel_py.review.routes import router as review_router
from trowel_py.statistics.routes import router as statistics_router
from trowel_py.telemetry.lifecycle import start_telemetry, stop_telemetry
from trowel_py.telemetry.routes import router as telemetry_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在应用生命周期内持有 CC 反向代理与可选后台组件。"""
    from trowel_py.resource_lifecycle import (
        DrainCoordinator,
        OwnerScope,
        ResourceRegistry,
        reconcile_previous_snapshot,
    )

    desktop_instance_id = str(app.state.desktop_instance_id).strip()
    app_instance_id = desktop_instance_id or f"browser-{uuid.uuid4().hex}"
    desktop_data_dir = getattr(app.state, "desktop_data_dir", None)
    snapshot_path = (
        Path(desktop_data_dir) / "resource-lifecycle.json"
        if desktop_instance_id and desktop_data_dir
        else None
    )
    if snapshot_path is not None:
        app.state.previous_reconcile_report = await asyncio.to_thread(
            reconcile_previous_snapshot,
            snapshot_path,
            current_instance_id=app_instance_id,
        )
    resource_registry = ResourceRegistry(
        app_instance_id=app_instance_id,
        snapshot_path=snapshot_path,
        registration_url=(
            f"http://127.0.0.1:{os.environ.get('TROWEL_SERVER_PORT', '8000')}"
            "/api/desktop/resources/register"
        ),
        registration_credential=getattr(app.state, "desktop_credential", None),
    )
    app.state.resource_registry = resource_registry
    start_telemetry(app)
    if snapshot_path is not None:
        resource_registry.register_process_group(
            resource_id=f"sidecar:{app_instance_id}",
            owner_scope=OwnerScope.APP,
            resource_kind="sidecar_process_group",
            pid=os.getpid(),
            runtime="app",
        )
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_env = load_settings_env(settings_path)
    real_base_url = settings_env.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    port = int(os.environ.get("TROWEL_SERVER_PORT", "8000"))
    app.state.cc_settings_path = settings_path
    app.state.cc_real_base_url = real_base_url
    app.state.proxy_base_url = f"http://127.0.0.1:{port}"
    app.state.cc_http_client = httpx.AsyncClient(timeout=httpx.Timeout(None))
    app.state.recent_workspace_store = RecentWorkspaceStore(
        resolve_recent_workspaces_path()
    )
    logger.info("[cc-proxy] TUI system fingerprint: %s", TUI_SYSTEM_IDENTITY[:40])
    logger.info(
        "[cc-proxy] upstream=%s via=%s", real_base_url, app.state.proxy_base_url
    )
    if bootstrap_layer_one():
        logger.info("[memory] seeded layer-one core.md (试用期)")
    # 可选后台组件必须隔离启动失败，避免局部配置或依赖问题阻断应用。
    try:
        from trowel_py.memory import paths as _mem_paths
        from trowel_py.memory.daily_review.scheduler import (
            MemoryReviewScheduler,
            load_review_config,
        )

        scheduler = MemoryReviewScheduler(
            load_review_config(),
            _mem_paths.resolve_memory_root(),
            resource_registry=resource_registry,
        )
        await scheduler.start()
        app.state.memory_scheduler = scheduler
    except Exception:
        logger.warning("[memory] review scheduler failed to start", exc_info=True)
        app.state.memory_scheduler = None
    # 后台提炼启动失败不能阻断应用。
    try:
        from trowel_py.memory import paths as _distill_paths
        from trowel_py.profile.distill.scheduler import (
            ProfileDistillScheduler,
            load_distill_config,
        )

        distill_scheduler = ProfileDistillScheduler(
            load_distill_config(),
            _distill_paths.resolve_memory_root(),
            app.state.proxy_base_url,
            app.state.cc_settings_path,
            resource_registry=resource_registry,
        )
        await distill_scheduler.start()
        app.state.distill_scheduler = distill_scheduler
    except Exception:
        logger.warning(
            "[memory] profile distill scheduler failed to start", exc_info=True
        )
        app.state.distill_scheduler = None
    try:
        from trowel_py.memory import paths as _tidy_paths
        from trowel_py.memory.tidy_scheduler import TidyScheduler
        from trowel_py.config import load_llm_config
        from trowel_py.llm.client import AnthropicProvider

        try:
            tidy_llm_config = load_llm_config()
        except FileNotFoundError:
            logger.info("[memory] tidy scheduler off: no LLM config")
            app.state.tidy_scheduler = None
        else:

            def _tidy_provider_factory():
                """创建 Memory 整理任务调用模型所用的客户端。"""

                return AnthropicProvider(tidy_llm_config)

            tidy_scheduler = TidyScheduler(
                _tidy_paths.resolve_memory_root(), _tidy_provider_factory
            )
            await tidy_scheduler.start()
            app.state.tidy_scheduler = tidy_scheduler
    except Exception:
        logger.warning("[memory] tidy scheduler failed to start", exc_info=True)
        app.state.tidy_scheduler = None
    # manager 延迟拉起 app-server；未使用 Codex 时不创建子进程。
    try:
        from trowel_py.codex_host import CodexHostManager

        app.state.codex_host_manager = CodexHostManager(
            resource_registry=resource_registry
        )
    except Exception:
        logger.warning("[codex] host manager init failed", exc_info=True)
        app.state.codex_host_manager = None
    app.state.quota_read_model = None
    app.state.quota_scheduler = None
    app.state.quota_http_client = None
    quota_observer = None
    try:
        from trowel_py.quota.codex import make_codex_observer
        from trowel_py.quota.glm import GlmQuotaClient, httpx_fetcher
        from trowel_py.quota.read_model import QuotaReadModel
        from trowel_py.quota.scheduler import QuotaScheduler, load_glm_accounts

        quota_read_model = QuotaReadModel()
        app.state.quota_read_model = quota_read_model
        quota_observer = make_codex_observer(quota_read_model)
        # poller 启动后会立即请求真实服务，因此只能通过 TROWEL_QUOTA_POLL=1 显式启用。
        quota_poll_enabled = os.environ.get("TROWEL_QUOTA_POLL") == "1"
        glm_accounts = load_glm_accounts() if quota_poll_enabled else []
        if glm_accounts:
            app.state.quota_http_client = httpx.AsyncClient(timeout=httpx.Timeout(None))
            quota_client = GlmQuotaClient(
                glm_accounts[0].host,
                fetcher=httpx_fetcher(app.state.quota_http_client),
            )
            quota_scheduler = QuotaScheduler(
                glm_accounts, quota_client, quota_read_model
            )
            await quota_scheduler.start()
            app.state.quota_scheduler = quota_scheduler
        elif quota_poll_enabled:
            logger.info("[quota] TROWEL_QUOTA_POLL set but no GLM account; poller idle")
        else:
            logger.info("[quota] GLM poller off (set TROWEL_QUOTA_POLL=1 to enable)")
    except Exception:
        logger.warning("[quota] read model failed to start", exc_info=True)
    app.state.agent_statistics_reader = None
    try:
        from trowel_py.agent_host import (
            BindingStore,
            Runtime,
            SessionBinding,
            SessionHub,
            resolve_bindings_path,
        )
        from trowel_py.agent_host.runtimes import (
            ClaudeCodeRuntimeAdapter,
            CodexRuntimeAdapter,
        )
        from trowel_py.agent_host.session_titles import NativeSessionTitleGenerator
        from trowel_py.cc_host.routes import get_registry
        from trowel_py.memory.paths import resolve_memory_root
        from trowel_py.statistics.agent.repository import FileAgentObservationReader

        cc_registry = get_registry()
        binding_store = BindingStore(resolve_bindings_path())
        runtime_ports = {
            Runtime.CLAUDE_CODE: ClaudeCodeRuntimeAdapter(cc_registry),
            Runtime.CODEX: CodexRuntimeAdapter(app.state.codex_host_manager),
        }

        def request_session_review(binding: SessionBinding) -> None:
            """持久登记关闭请求，并在可用时唤醒当前 Memory worker。"""

            scheduler = app.state.memory_scheduler
            if scheduler is not None:
                scheduler.request_session_review(binding)
                return
            from trowel_py.memory import paths as memory_paths
            from trowel_py.memory.daily_review.requests import enqueue_session_review

            enqueue_session_review(memory_paths.resolve_memory_root(), binding)

        try:
            codex_history_root = resolve_memory_root()
        except Exception:  # noqa: BLE001 - 配置异常不能让整个 Agent Hub 停用。
            logger.warning(
                "[agent] Codex normalized history disabled because Memory root "
                "could not be resolved",
                exc_info=True,
            )
            codex_history_root = None

        app.state.agent_statistics_reader = (
            FileAgentObservationReader(codex_history_root, binding_store)
            if codex_history_root is not None
            else None
        )
        app.state.agent_hub = SessionHub(
            binding_store,
            codex_manager=app.state.codex_host_manager,
            cc_registry=cc_registry,
            cc_proxy_base_url=app.state.proxy_base_url,
            cc_settings_path=app.state.cc_settings_path,
            event_observer=quota_observer,
            runtime_ports=runtime_ports,
            session_review_requester=request_session_review,
            title_generator=NativeSessionTitleGenerator(
                codex_manager=app.state.codex_host_manager,
                cc_proxy_base_url=app.state.proxy_base_url,
                cc_settings_path=app.state.cc_settings_path,
                resource_registry=resource_registry,
            ),
            codex_history_root=codex_history_root,
            resource_registry=resource_registry,
            runtime_availability=detect_runtime_availability(),
        )
    except Exception:
        logger.warning("[agent] session hub init failed", exc_info=True)
        app.state.agent_hub = None
    app.state.drain_coordinator = DrainCoordinator(
        resource_registry=resource_registry,
        agent_hub=app.state.agent_hub,
        schedulers=tuple(
            (name, component)
            for name, component in (
                ("memory_scheduler", app.state.memory_scheduler),
                ("profile_distill_scheduler", app.state.distill_scheduler),
                ("memory_tidy_scheduler", app.state.tidy_scheduler),
                ("quota_scheduler", app.state.quota_scheduler),
            )
            if component is not None
        ),
        codex_manager=app.state.codex_host_manager,
    )
    yield
    try:
        report = await app.state.drain_coordinator.drain()
        if report.status != "closed":
            logger.warning("[app] shutdown needs resource reconciliation: %s", report)
    except Exception:
        logger.warning("[app] coordinated drain failed", exc_info=True)
    await asyncio.to_thread(stop_telemetry, app, timeout_seconds=1.0)
    _quota_http = getattr(app.state, "quota_http_client", None)
    if _quota_http is not None:
        try:
            await _quota_http.aclose()
        except Exception:
            logger.warning("[quota] http client close failed", exc_info=True)
    await app.state.cc_http_client.aclose()


def bootstrap_layer_one() -> bool:
    """幂等创建 layer-one ``core.md``，且任何失败都不阻断应用启动。"""
    try:
        from trowel_py.memory import paths, seeds

        return seeds.bootstrap_core(paths.resolve_memory_root())
    except Exception:
        logger.warning("[memory] layer-one bootstrap failed", exc_info=True)
        return False


def create_app() -> FastAPI:
    """创建 FastAPI 应用，并注册中间件、路由和静态前端。"""

    app = FastAPI(lifespan=lifespan)
    desktop_credential = os.environ.pop("TROWEL_DESKTOP_CREDENTIAL", None)
    desktop_renderer_origin = os.environ.pop(
        "TROWEL_DESKTOP_RENDERER_ORIGIN", None
    )
    app.state.desktop_instance_id = os.environ.pop(
        "TROWEL_APP_INSTANCE_ID", ""
    )
    app.state.desktop_data_dir = os.environ.get("TROWEL_DESKTOP_DATA_DIR", "").strip()
    app.state.desktop_credential = desktop_credential

    app.add_middleware(
        DesktopCredentialMiddleware,
        credential=desktop_credential,
    )

    from fastapi.middleware.cors import CORSMiddleware

    allowed_origins = [
        "http://localhost:5173",
        "http://localhost:5174",
    ]
    if desktop_renderer_origin:
        allowed_origins.append(
            validate_desktop_renderer_origin(desktop_renderer_origin)
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        """返回后端存活状态。"""

        return {
            "success": True,
            "data": {"status": "ok"},
            "error": None,
        }

    @app.exception_handler(Exception)
    def global_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """将未处理异常转换成统一的 500 错误响应。"""

        logger.error(
            "Unhandled exception on %s %s: %s", request.method, request.url.path, exc
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "data": None,
                "error": str(exc),
            },
        )

    app.include_router(card_router, prefix="/api/cards")
    app.include_router(review_router, prefix="/api/review")
    app.include_router(garden_router, prefix="/api/garden")
    app.include_router(player_router, prefix="/api/player")
    app.include_router(profile_router, prefix="/api/profile")
    app.include_router(events_router, prefix="/api/events")
    app.include_router(pet_router, prefix="/api/pet")
    app.include_router(feynman_router, prefix="/api/feynman")
    app.include_router(proxy_router)
    app.include_router(cc_host_router, prefix="/api/cc")
    app.include_router(agent_router, prefix="/api/agent")
    app.include_router(quota_router)
    app.include_router(desktop_router, prefix="/api/desktop")
    app.include_router(telemetry_router, prefix="/api/telemetry")
    app.include_router(statistics_router, prefix="/api/statistics")

    # 发布安装由后端托管构建产物；开发模式没有产物时由 Vite 独立提供前端。
    web_dist = _find_web_dist()
    if web_dist is not None:
        index_html = web_dist / "index.html"

        # 非 API 路径回退到 index.html，使前端路由刷新后仍能恢复。
        @app.get("/{full_path:path}")
        def _spa_fallback(full_path: str) -> object:
            """为未匹配的路径返回前端资源，API 路径则返回 JSON 404。

            非 API 路径存在静态文件时直接返回，否则回退到 SPA 入口。

            Args:
                full_path: 请求路径中去掉开头斜杠后的部分。
            """

            if full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "data": None, "error": "not found"},
                )
            # 候选文件必须留在 web_dist 内；越界路径回退到 SPA，不能读取宿主文件。
            root = web_dist.resolve()
            candidate = (web_dist / full_path).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return FileResponse(
                    index_html, headers=_static_cache_headers(index_html, root)
                )
            if candidate.is_file():
                return FileResponse(
                    candidate, headers=_static_cache_headers(candidate, root)
                )
            return FileResponse(
                index_html, headers=_static_cache_headers(index_html, root)
            )

    return app


def _resolve_web_dist(here: Path) -> Path | None:
    """优先使用源码树的新构建，避免 editable install 读取过期的 static 快照。"""
    for candidate in (here.parent / "web" / "dist", here / "static"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _find_web_dist() -> Path | None:
    """查找当前安装位置可用的前端构建目录。"""

    return _resolve_web_dist(Path(__file__).resolve().parent)


def _static_cache_headers(file_path: Path, root: Path) -> dict[str, str]:
    """入口文件必须重新验证；带内容哈希的 Vite 资源可长期缓存。"""
    try:
        rel = file_path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path()
    if rel.parts and rel.parts[0] == "assets":
        return {"cache-control": "public, max-age=31536000, immutable"}
    return {"cache-control": "no-cache"}
