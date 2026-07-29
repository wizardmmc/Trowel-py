"""保存重生成计划、运行 manifest 和发布记录使用的 JSON 文件。

本模块的路径函数只做拼接，不校验 plan_id 或 run_id；绝对标识或包含 ``..``
的标识可能逃出重生成目录，调用方必须先保证标识可信。
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .models import RegenerationPlan, RegenerationRun


def regeneration_root(root: Path | str) -> Path:
    """返回 memory 根目录下的重生成元数据目录。"""
    return Path(root) / "meta" / "regeneration"


def plan_path(root: Path | str, plan_id: str) -> Path:
    """用未经校验的 plan_id 拼出计划 JSON 路径。"""
    return regeneration_root(root) / "plans" / f"{plan_id}.json"


def run_root(root: Path | str, run_id: str) -> Path:
    """用未经校验的 run_id 拼出运行工作目录。"""
    return regeneration_root(root) / "runs" / run_id


def run_path(root: Path | str, run_id: str) -> Path:
    """用未经校验的 run_id 拼出 ``manifest.json`` 路径。"""
    return run_root(root, run_id) / "manifest.json"


def apply_path(root: Path | str, run_id: str) -> Path:
    """用未经校验的 run_id 拼出幂等发布记录路径。"""
    return run_root(root, run_id) / "apply.json"


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """把 JSON 对象写入同目录临时文件，再原子替换目标。

    输出使用 UTF-8 和两空格缩进，保留非 ASCII 字符并以换行结尾。序列化、
    建目录、写入、替换或清理临时文件的异常均向上传播。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict[str, Any]:
    """读取 UTF-8 JSON，并要求顶层值是对象。

    Raises:
        ValueError: JSON 无效或顶层不是对象。
        UnicodeError: 文件不是有效 UTF-8。
        OSError: 文件读取失败。
    """
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return value


def save_plan(root: Path | str, plan: RegenerationPlan) -> None:
    """目标已存在时抛出 ``FileExistsError``，否则原子写入计划。

    ``exists`` 检查与最终替换之间没有加锁；并发写入相同 plan_id 时仍可能
    互相覆盖。
    """
    path = plan_path(root, plan.plan_id)
    if path.exists():
        raise FileExistsError(f"regeneration plan already exists: {plan.plan_id}")
    atomic_write_json(path, plan.to_dict())


def load_plan(root: Path | str, plan_id: str) -> RegenerationPlan:
    """按标识读取计划，并通过模型的局部校验恢复对象。

    Raises:
        FileNotFoundError: 对应计划路径不指向文件。
        ValueError: JSON 或计划字段无效。
        TypeError: 嵌套目标字段不支持模型要求的转换。
        OverflowError: 嵌套目标的版本值在整数转换时溢出。
        UnicodeError: 计划文件不是有效 UTF-8。
        OSError: 计划路径检查或文件读取失败。
    """
    path = plan_path(root, plan_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown regeneration plan: {plan_id}")
    return RegenerationPlan.from_dict(read_json(path))


def save_run(root: Path | str, run: RegenerationRun) -> None:
    """无条件原子替换运行 manifest；同 run_id 的已有记录会被覆盖。"""
    atomic_write_json(run_path(root, run.run_id), run.to_dict())


def load_run(root: Path | str, run_id: str) -> RegenerationRun:
    """按标识读取运行 manifest，并通过模型的局部校验恢复对象。

    Raises:
        FileNotFoundError: 对应 manifest 路径不指向文件。
        ValueError: JSON 或运行字段无效。
        UnicodeError: manifest 不是有效 UTF-8。
        OSError: manifest 路径检查或文件读取失败。
    """
    path = run_path(root, run_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown regeneration run: {run_id}")
    return RegenerationRun.from_dict(read_json(path))
