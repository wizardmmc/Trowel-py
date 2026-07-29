"""为画像路由创建 Memory 仓储，并把完整画像更新写入文件。"""

from __future__ import annotations

from datetime import date

from trowel_py.memory.paths import resolve_memory_root
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile
from trowel_py.profile.schemas import ProfileUpdate


def get_profile_store() -> MemoryStore:
    """解析配置或默认的 Memory 根目录，并创建画像仓储。

    返回的仓储会直接读写该目录。路由和集成测试必须通过 FastAPI
    ``dependency_overrides`` 将它替换为临时仓储；配置和路径解析异常原样传播。
    """
    return MemoryStore(resolve_memory_root())


def write_profile(store: MemoryStore, update: ProfileUpdate) -> Profile:
    """用请求中的五个维度完整替换画像，并记录来源和服务器本地日期。

    如果 ``profile.md`` 已存在，仓储会先保存历史快照再覆盖文件。校验和文件
    I/O 异常原样传播。

    Args:
        store: 目标 Memory 仓储；其根目录决定画像文件和历史快照的位置。
        update: 五个画像维度及本次写入来源；未提供的维度已填为空字符串。

    Returns:
        重新读取的画像，其中日期和来源是本次实际写入的值。

    Raises:
        ValueError: 画像内容、更新时间或来源未通过仓储校验。
        OSError: 快照、覆盖写入或重新读取画像文件失败。
        UnicodeError: 画像文件无法按 UTF-8 编解码。
    """
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
