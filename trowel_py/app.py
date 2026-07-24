import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from trowel_py.agent_host.routes import router as agent_router
from trowel_py.quota.routes import router as quota_router
from trowel_py.cards.routes import router as card_router
from trowel_py.cc_host.proxy import (
    TUI_SYSTEM_IDENTITY,
    load_settings_env,
    router as proxy_router,
)
from trowel_py.cc_host.routes import router as cc_host_router
from trowel_py.events.routes import router as events_router
from trowel_py.feynman.routes import router as feynman_router
from trowel_py.garden.routes import router as garden_router
from trowel_py.pet.routes import router as pet_router
from trowel_py.player.routes import router as player_router
from trowel_py.profile.routes import router as profile_router
from trowel_py.review.routes import router as review_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在应用生命周期内持有 CC 反向代理与可选后台组件。"""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_env = load_settings_env(settings_path)
    real_base_url = settings_env.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    port = int(os.environ.get("TROWEL_SERVER_PORT", "8000"))
    app.state.cc_settings_path = settings_path
    app.state.cc_real_base_url = real_base_url
    app.state.proxy_base_url = f"http://127.0.0.1:{port}"
    app.state.cc_http_client = httpx.AsyncClient(timeout=httpx.Timeout(None))
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
            load_review_config(), _mem_paths.resolve_memory_root()
        )
        await scheduler.start()
        app.state.memory_scheduler = scheduler
    except Exception:
        logger.warning("[memory] review scheduler failed to start", exc_info=True)
        app.state.memory_scheduler = None
    # 后台提炼启动失败不能阻断应用。
    try:
        from trowel_py.memory import paths as _distill_paths
        from trowel_py.memory.profile_distill.scheduler import (
            ProfileDistillScheduler,
            load_distill_config,
        )

        distill_scheduler = ProfileDistillScheduler(
            load_distill_config(),
            _distill_paths.resolve_memory_root(),
            app.state.proxy_base_url,
            app.state.cc_settings_path,
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

        def _tidy_provider_factory():
            from trowel_py.config import load_llm_config
            from trowel_py.llm.client import AnthropicProvider

            return AnthropicProvider(load_llm_config())

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

        app.state.codex_host_manager = CodexHostManager()
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
    try:
        from trowel_py.agent_host import (
            BindingStore,
            SessionHub,
            resolve_bindings_path,
        )

        app.state.agent_hub = SessionHub(
            BindingStore(resolve_bindings_path()),
            codex_manager=app.state.codex_host_manager,
            cc_proxy_base_url=app.state.proxy_base_url,
            cc_settings_path=app.state.cc_settings_path,
            event_observer=quota_observer,
        )
    except Exception:
        logger.warning("[agent] session hub init failed", exc_info=True)
        app.state.agent_hub = None
    yield
    _scheduler = getattr(app.state, "memory_scheduler", None)
    if _scheduler is not None:
        try:
            await _scheduler.stop()
        except Exception:
            logger.warning("[memory] review scheduler stop failed", exc_info=True)
    _distill = getattr(app.state, "distill_scheduler", None)
    if _distill is not None:
        try:
            await _distill.stop()
        except Exception:
            logger.warning(
                "[memory] profile distill scheduler stop failed", exc_info=True
            )
    _tidy = getattr(app.state, "tidy_scheduler", None)
    if _tidy is not None:
        try:
            await _tidy.stop()
        except Exception:
            logger.warning("[memory] tidy scheduler stop failed", exc_info=True)
    _codex_mgr = getattr(app.state, "codex_host_manager", None)
    if _codex_mgr is not None:
        try:
            await _codex_mgr.close()
        except Exception:
            logger.warning("[codex] host manager close failed", exc_info=True)
    _quota_sched = getattr(app.state, "quota_scheduler", None)
    if _quota_sched is not None:
        try:
            await _quota_sched.stop()
        except Exception:
            logger.warning("[quota] scheduler stop failed", exc_info=True)
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
    app = FastAPI(lifespan=lifespan)

    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:5174",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "success": True,
            "data": {"status": "ok"},
            "error": None,
        }

    @app.exception_handler(Exception)
    def global_error_handler(request: Request, exc: Exception) -> JSONResponse:
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

    # 发布安装由后端托管构建产物；开发模式没有产物时由 Vite 独立提供前端。
    web_dist = _find_web_dist()
    if web_dist is not None:
        index_html = web_dist / "index.html"

        # 非 API 路径回退到 index.html，使前端路由刷新后仍能恢复。
        @app.get("/{full_path:path}")
        def _spa_fallback(full_path: str) -> object:
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
