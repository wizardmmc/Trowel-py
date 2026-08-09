"""确认 moon 是否从任务所属项目目录启动叶子进程。"""

from __future__ import annotations

from pathlib import Path


def main() -> int:
    """仅在当前目录与脚本所属 web 项目相同时通过。"""
    expected = Path(__file__).resolve().parent
    current = Path.cwd().resolve()
    if current != expected:
        print(f"wrong cwd: expected {expected}, got {current}")
        return 7
    print(f"cwd passed: {current}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
