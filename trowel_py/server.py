import uvicorn

from trowel_py.app import create_app
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations

import logging
import sys
from pathlib import Path


def setup_logging() -> None:
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
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Trowel server")

    conn = create_db()
    run_migrations(conn)
    conn.close()  # 释放迁移写锁，后续请求才能写入。
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    bootstrap()
