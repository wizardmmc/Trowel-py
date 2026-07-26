"""从 Memory/Profile 的模型输入副本中移除 Kernel 控制消息。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from trowel_py.kernel_messages import KERNEL_SOFT_YIELD_MARKER


def is_kernel_control_record(raw: bytes) -> bool:
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(value, dict) or value.get("type") != "user":
        return False
    message = value.get("message")
    if isinstance(message, dict):
        if message.get("role") != "user":
            return False
        return _has_control_prefix(message.get("content"))
    payload = value.get("payload")
    return isinstance(payload, dict) and _has_control_prefix(payload.get("text"))


def materialize_memory_safe_source(source: Path, workdir: Path) -> Path:
    """生成等长 JSONL 副本，使原字节 offset 仍可用于增量范围。"""

    raw = source.read_bytes()
    sanitized = b"".join(
        _blank_record(line) if is_kernel_control_record(line) else line
        for line in raw.splitlines(keepends=True)
    )
    identity = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    target = workdir / f"source-{identity}.memory-safe.jsonl"
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_bytes(sanitized)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _has_control_prefix(value: Any) -> bool:
    if isinstance(value, str):
        return value.lstrip().startswith(KERNEL_SOFT_YIELD_MARKER)
    if isinstance(value, list):
        return any(_has_control_prefix(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "text":
            return _has_control_prefix(value.get("text"))
        return False
    return False


def _blank_record(line: bytes) -> bytes:
    if line.endswith(b"\r\n"):
        return b" " * (len(line) - 2) + b"\r\n"
    if line.endswith(b"\n"):
        return b" " * (len(line) - 1) + b"\n"
    return b" " * len(line)
