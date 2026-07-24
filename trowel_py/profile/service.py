"""profile HTTP 层对 memory store 的轻量适配。"""

from __future__ import annotations

from datetime import date

from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile
from trowel_py.profile.schemas import ProfileUpdate


def get_profile_store() -> MemoryStore:
    """解析当前 memory root；测试必须覆盖此依赖以隔离真实用户目录。"""
    return MemoryStore(resolve_memory_root())


def write_profile(store: MemoryStore, update: ProfileUpdate) -> Profile:
    """写入来源和服务端日期，再重新读取以返回实际落盘内容。"""
    profile = Profile(
        ability=update.ability,
        methodology=update.methodology,
        expression=update.expression,
        goal=update.goal,
        other=update.other,
        updated=date.today().isoformat(),
    )
    store.write_profile(profile, source=update.source)
    return store.load_profile()
