import sqlite3
from pathlib import Path

def run_migrations(conn: sqlite3.Connection, migrations_dir: str | None = None) -> None:
    """
    实现一个迁移运行器，扫描 trowel_py/db/migrations 里的.sql文件，按照文件名排序，依次执行，已经执行过的不再重复执行
    """
    # 确定迁移目录路径，默认当前路径下的 migrations
    if migrations_dir is None:
        migrations_dir = str(Path(__file__).parent / "migrations")

    # 创建 _migrations 表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations(
            name TEXT PRIMARY KEY
        )""")

    # 查询已执行
    executed = {row["name"] for row in conn.execute("SELECT name from _migrations").fetchall()}

    # 扫描 .sql 文件，排序
    files = Path(migrations_dir).glob("*.sql")
    sorted_files = sorted(files)

    # 逐个执行未跑过的 .sql 文件
    for i in sorted_files:
        if i.name not in executed:
            conn.executescript(i.read_text())   # 兼容执行多条语句
            conn.execute("INSERT INTO _migrations (name) VALUES (?)", (i.name,))    # (i.name,) - 构建只包含 i.name 一个元素的元组
