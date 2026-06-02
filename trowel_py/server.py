import uvicorn

from trowel_py.app import create_app
from trowel_py.db.connection import create_db
from trowel_py.db.migrate import run_migrations
# 整个程序的入口

def bootstrap() -> None:
    conn = create_db()
    run_migrations(conn)
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    bootstrap()
