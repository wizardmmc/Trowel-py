import uvicorn

from trowel_py.app import create_app
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations

# system level log
import logging
import sys
from pathlib import Path
def setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    # configuration
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / "trowel.log"),
            logging.StreamHandler(sys.stdout),  # Allow simultaneous output to the terminal
        ],
    )

# 整个程序的入口

def bootstrap() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting Trowel server")

    conn = create_db()
    run_migrations(conn)
    conn.close()  # release write lock so request connections can write
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    bootstrap()
