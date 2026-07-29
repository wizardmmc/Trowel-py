#!/usr/bin/env python3
"""报告 Python 源码中缺少模块、类或函数 docstring 的位置。"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MissingDocstring:
    """记录一个缺少 docstring 的源码定义。"""

    path: Path
    line: int
    kind: str
    name: str


class _DefinitionVisitor(ast.NodeVisitor):
    """收集一个模块中缺少 docstring 的类和函数。"""

    def __init__(self, path: Path) -> None:
        """为指定源码文件创建收集器。"""

        self._path = path
        self._parents: list[str] = []
        self.missing: list[MissingDocstring] = []

    def _record(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """在当前定义没有 docstring 时记录其完整名称。"""

        if ast.get_docstring(node, clean=False) is not None:
            return
        name = ".".join((*self._parents, node.name))
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        self.missing.append(
            MissingDocstring(self._path, node.lineno, kind, name)
        )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """检查类本身，并继续检查它包含的定义。"""

        self._record(node)
        self._parents.append(node.name)
        self.generic_visit(node)
        self._parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """检查同步函数，并继续检查其中的嵌套定义。"""

        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """检查异步函数，并继续检查其中的嵌套定义。"""

        self._visit_function(node)

    def _visit_function(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        """用相同规则检查同步和异步函数。"""

        self._record(node)
        self._parents.append(node.name)
        self.generic_visit(node)
        self._parents.pop()


def _python_files(paths: Iterable[Path]) -> list[Path]:
    """展开输入路径并返回去重、排序后的 Python 文件。"""

    files: set[Path] = set()
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            files.add(path)
        elif path.is_dir():
            files.update(path.rglob("*.py"))
    return sorted(files)


def audit(paths: Iterable[Path]) -> tuple[list[Path], list[MissingDocstring]]:
    """检查输入路径下的模块、类和函数是否都有 docstring。"""

    files = _python_files(paths)
    missing: list[MissingDocstring] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if ast.get_docstring(tree, clean=False) is None:
            missing.append(MissingDocstring(path, 1, "module", path.stem))
        visitor = _DefinitionVisitor(path)
        visitor.visit(tree)
        missing.extend(visitor.missing)
    return files, missing


def main() -> int:
    """运行命令行审计，并以退出码表示是否仍有缺失。"""

    parser = argparse.ArgumentParser(
        description="检查 Python 模块、类和函数是否都有 docstring。"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("trowel_py")],
        help="要检查的 Python 文件或目录，默认检查 trowel_py",
    )
    args = parser.parse_args()
    files, missing = audit(args.paths)
    for item in missing:
        print(f"{item.path}:{item.line}: {item.kind} {item.name}")
    print(f"checked={len(files)} missing={len(missing)}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
