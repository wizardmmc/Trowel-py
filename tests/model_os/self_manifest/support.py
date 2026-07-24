from __future__ import annotations

from typing import Any

from trowel_py.model_os.self_assembler import build_self_manifest
from trowel_py.model_os.types import SelfManifest


def build_manifest(**overrides: Any) -> SelfManifest:
    arguments: dict[str, Any] = {
        "runtime": "cc",
        "model": "claude-sonnet-5",
        "effort": None,
        "memory_enabled": True,
        "profile_enabled": True,
    }
    arguments.update(overrides)
    return build_self_manifest(**arguments)
