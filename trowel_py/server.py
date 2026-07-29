"""配置日志、迁移数据库，并在固定本地地址启动 Trowel 后端。"""

import uvicorn

from trowel_py.app import create_app
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations

import logging
import sys
from pathlib import Path


def setup_logging() -> None:
    """根 logger 尚无 handler 时，为其添加文件和标准输出 handler。

    同时把根 logger 的级别设为 INFO；文件 handler 写入当前工作目录下的
    ``logs/trowel.log``。
    """
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / "trowel.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def bootstrap() -> None:
    """配置日志、迁移当前目录的 ``trowel.db``，并启动本地 FastAPI 应用。

    服务固定监听 ``127.0.0.1:8000``。
    """
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Trowel server")

    conn = create_db()
    run_migrations(conn)
    conn.close()
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    bootstrap()
