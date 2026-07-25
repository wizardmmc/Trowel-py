"""重生成 manifest 的原子文件存储。"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .models import RegenerationPlan, RegenerationRun


def regeneration_root(root: Path | str) -> Path:
    return Path(root) / "meta" / "regeneration"


def plan_path(root: Path | str, plan_id: str) -> Path:
    return regeneration_root(root) / "plans" / f"{plan_id}.json"


def run_root(root: Path | str, run_id: str) -> Path:
    return regeneration_root(root) / "runs" / run_id


def run_path(root: Path | str, run_id: str) -> Path:
    return run_root(root, run_id) / "manifest.json"


def apply_path(root: Path | str, run_id: str) -> Path:
    return run_root(root, run_id) / "apply.json"


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
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
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"manifest must be a JSON object: {path}")
    return value


def save_plan(root: Path | str, plan: RegenerationPlan) -> None:
    path = plan_path(root, plan.plan_id)
    if path.exists():
        raise FileExistsError(f"regeneration plan already exists: {plan.plan_id}")
    atomic_write_json(path, plan.to_dict())


def load_plan(root: Path | str, plan_id: str) -> RegenerationPlan:
    path = plan_path(root, plan_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown regeneration plan: {plan_id}")
    return RegenerationPlan.from_dict(read_json(path))


def save_run(root: Path | str, run: RegenerationRun) -> None:
    atomic_write_json(run_path(root, run.run_id), run.to_dict())


def load_run(root: Path | str, run_id: str) -> RegenerationRun:
    path = run_path(root, run_id)
    if not path.is_file():
        raise FileNotFoundError(f"unknown regeneration run: {run_id}")
    return RegenerationRun.from_dict(read_json(path))
