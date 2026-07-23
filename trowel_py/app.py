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
    """slice-030: spin up the local reverse-proxy resources the CC subprocess
    routes through. Built once on startup, torn down on shutdown.

    - shared httpx.AsyncClient (timeout=None) for all /v1/* forwards
    - real upstream base_url + settings_path read from ~/.claude/settings.json
    - proxy_base_url = http://127.0.0.1:<TROWEL_SERVER_PORT> (CC targets this)
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_env = load_settings_env(settings_path)
    real_base_url = settings_env.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    port = int(os.environ.get("TROWEL_SERVER_PORT", "8000"))
    app.state.cc_settings_path = settings_path
    app.state.cc_real_base_url = real_base_url
    app.state.proxy_base_url = f"http://127.0.0.1:{port}"
    app.state.cc_http_client = httpx.AsyncClient(timeout=httpx.Timeout(None))
    logger.info("[cc-proxy] TUI system fingerprint: %s", TUI_SYSTEM_IDENTITY[:40])
    logger.info("[cc-proxy] upstream=%s via=%s", real_base_url, app.state.proxy_base_url)
    # slice-039: ensure layer-one core.md exists before any cc session injects it.
    if bootstrap_layer_one():
        logger.info("[memory] seeded layer-one core.md (试用期)")
    # slice-046: in-app daily memory review scheduler (replaces launchd). Fires
    # a startup catchup + a daily fixed-time run; any failure is swallowed
    # (C-5) so the app still starts. start() only schedules background tasks
    # (C-1, non-blocking).
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
    # slice-050: in-app daily profile distill scheduler (sister of the review
    # scheduler). Carries proxy_base_url so distill goes through the trowel proxy
    # (C-4 — 529 prep). Any failure is swallowed (C-6) so the app still starts.
    try:
        from trowel_py.memory import paths as _distill_paths
        from trowel_py.memory.profile_distill_scheduler import (
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
        logger.warning("[memory] profile distill scheduler failed to start", exc_info=True)
        app.state.distill_scheduler = None
    # slice-052: weekly/monthly tidy scheduler. Unlike review/distill it does
    # NOT spawn cc — Python calls the provider directly (C-4) — so it just needs
    # a provider_factory. Fires weekly Mon 03:30 / monthly 1st 04:00 on
    # completed intervals; any failure is swallowed (C-5, same as the others).
    try:
        from trowel_py.memory import paths as _tidy_paths
        from trowel_py.memory.tidy_scheduler import TidyScheduler

        def _tidy_provider_factory():  # -> AnthropicProvider(load_llm_config())
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
    # slice-071: shared Codex app-server manager. Lazy — only spawns the
    # app-server process on the first Codex send, so opening trowel without
    # using Codex costs nothing. Shutdown always closes it. Failure is swallowed
    # so a missing/old Codex install never blocks app startup.
    try:
        from trowel_py.codex_host import CodexHostManager

        app.state.codex_host_manager = CodexHostManager()
    except Exception:
        logger.warning("[codex] host manager init failed", exc_info=True)
        app.state.codex_host_manager = None
    # slice-093-pre: cross-provider quota read model. GLM accounts are polled
    # every 5 min; Codex/GPT ``rate_limit_updated`` pushes fold in via the hub
    # observer. Failure is swallowed so a missing/bad config never blocks
    # startup (same posture as the other schedulers).
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
        # GLM polling is opt-in (TROWEL_QUOTA_POLL=1): the poller hits the real
        # provider immediately on start, so it must NOT run during tests / dev
        # boots that only need the read model + Codex observer. Conservative v0
        # posture — flip on to observe, matches the "先保守后放开" preference.
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
    # slice-072: host-neutral Session Hub. Wires the binding store to the
    # shared Codex manager + cc_host's live _REGISTRY (the default cc_registry
    # / cc_opener point at cc_host.routes). Lazy like the codex manager —
    # opening trowel costs nothing until a session is created. Failure is
    # swallowed so a missing piece never blocks app startup.
    try:
        from trowel_py.agent_host import (
            BindingStore,
            SessionHub,
            resolve_bindings_path,
        )

        app.state.agent_hub = SessionHub(
            BindingStore(resolve_bindings_path()),
            codex_manager=app.state.codex_host_manager,
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
    """Ensure layer-one ``core.md`` exists at the memory root (slice-039).

    Idempotent — ``seeds.bootstrap_core`` refuses to overwrite a reviewed
    core.md (C-5: layer-one pollution = whole-system pollution). Called from
    ``lifespan`` on startup so the first cc session has a layer-one to inject.
    Returns False on any failure (never breaks startup) or when the seed
    already existed.
    """
    try:
        from trowel_py.memory import paths, seeds

        return seeds.bootstrap_core(paths.resolve_memory_root())
    except Exception:
        logger.warning("[memory] layer-one bootstrap failed", exc_info=True)
        return False


# fastapi 应用工厂


def create_app() -> FastAPI:
    """
    构建并返回 FastAPI 实例，工厂模式
    FastAPI 注册了相关函数，可以被外界调用
    """
    app = FastAPI(lifespan=lifespan)

    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:5174",
        ],  # vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")  # 装饰器 - 将装饰的函数注释给某个系统使用
    def health() -> dict[str, object]:
        return {
            "success": True,
            "data": {"status": "ok"},
            "error": None,
        }  # 默认 200 状态码

    @app.exception_handler(Exception)
    def global_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """
        全局错误处理器（处理未被捕获的异常）
        @param: exc - 异常实例的句柄
        """
        logger.error(
            "Unhandled exception on %s %s: %s", request.method, request.url.path, exc
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "data": None,
                "error": str(exc),  # 把异常信息转成字符串
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

    # Serve the built frontend when present (release / `pip install` mode).
    # Skipped in dev — vite serves the frontend on :5173 and proxies /api here.
    web_dist = _find_web_dist()
    if web_dist is not None:
        index_html = web_dist / "index.html"

        # SPA fallback: any non-/api/* GET returns index.html so frontend
        # routes (e.g. /cc) survive a browser refresh. API misses still 404.
        # Registered after all /api routers so it never shadows a real route.
        @app.get("/{full_path:path}")
        def _spa_fallback(full_path: str) -> object:
            if full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "data": None, "error": "not found"},
                )
            # Confine to web_dist to block path traversal (GET /../secret.txt
            # would otherwise read any file the backend user can). Escape →
            # index.html so the SPA still renders.
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
    """Pick the built-frontend dir relative to ``here`` (the ``trowel_py/``
    package dir). Source-tree ``web/dist`` wins over the packaged
    ``trowel_py/static`` copy: ``web/dist`` is Vite's freshest output (what a
    dev just rebuilt), while ``static`` is a ``pip install .`` snapshot that
    goes stale in editable installs. When only one exists (a packaged
    install ships no ``web/``; a source tree may lack ``static``), that one
    is used; when neither exists, returns None (dev mode, no build yet).

    The prior static-first order silently served a stale copy whenever an
    editable install left an old ``trowel_py/static`` around — dev changes
    appeared to "not take effect" until the static copy was manually resynced.
    """
    for candidate in (here.parent / "web" / "dist", here / "static"):
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _find_web_dist() -> Path | None:
    """Locate the built frontend for the running install (see
    ``_resolve_web_dist`` for the priority order)."""
    return _resolve_web_dist(Path(__file__).resolve().parent)


def _static_cache_headers(file_path: Path, root: Path) -> dict[str, str]:
    """Cache-Control headers for a file served by the SPA fallback.

    Without an explicit Cache-Control, FileResponse only sends Last-Modified /
    ETag and the browser heuristic-caches the entry index.html — so after a
    rebuild the browser keeps using the stale index.html (which still points
    at the old hashed JS bundle) until the user force-refreshes. Setting
    per-file Cache-Control fixes that.

    - index.html and other non-hashed files -> ``no-cache``: revalidate every
      request so a rebuild's new ``<script src="assets/index-NewHash.js">``
      reaches the browser immediately.
    - ``assets/*-<hash>.*`` -> ``public, max-age=31536000, immutable``: Vite
      content-addresses these (content change => hash change => new filename),
      so an old URL is never served new content — safe to cache for a year.

    Args:
        file_path: the file being served.
        root: the web_dist root, used to compute the path relative to the
            served directory.

    Returns:
        A single-entry ``{"cache-control": ...}`` dict for
        ``FileResponse(headers=...)``.
    """
    try:
        rel = file_path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path()
    if rel.parts and rel.parts[0] == "assets":
        return {"cache-control": "public, max-age=31536000, immutable"}
    return {"cache-control": "no-cache"}
