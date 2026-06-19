from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from trowel_py.cards.routes import router as card_router
from trowel_py.review.routes import router as review_router
from trowel_py.garden.routes import router as garden_router
from trowel_py.player.routes import router as player_router
from trowel_py.events.routes import router as events_router
import logging

logger = logging.getLogger(__name__)

# fastapi 应用工厂

def create_app() -> FastAPI:
    """
    构建并返回 FastAPI 实例，工厂模式
    FastAPI 注册了相关函数，可以被外界调用
    """
    app = FastAPI()

    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:5174"],    # vite dev server
        allow_methods=["*"],
        allow_headers=["*"]
    )

    @app.get("/api/health") # 装饰器 - 将装饰的函数注释给某个系统使用
    def health() -> dict[str, object]:
        return {
            "success": True,
            "data": {
                "status": "ok"
            },
            "error": None
        }   # 默认 200 状态码
    
    @app.exception_handler(Exception)
    def global_error_handler(request: Request, exc: Exception) -> JSONResponse:
        """
        全局错误处理器（处理未被捕获的异常）
        @param: exc - 异常实例的句柄
        """
        logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "data": None,
                "error": str(exc)   # 把异常信息转成字符串
            }
        )
    
    app.include_router(card_router, prefix="/api/cards")
    app.include_router(review_router, prefix="/api/review")
    app.include_router(garden_router, prefix="/api/garden")
    app.include_router(player_router, prefix="/api/player")
    app.include_router(events_router, prefix="/api/events")

    return app