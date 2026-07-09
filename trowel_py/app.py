import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

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
    yield
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
    app.include_router(events_router, prefix="/api/events")
    app.include_router(pet_router, prefix="/api/pet")
    app.include_router(feynman_router, prefix="/api/feynman")
    app.include_router(proxy_router)
    app.include_router(cc_host_router, prefix="/api/cc")

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
