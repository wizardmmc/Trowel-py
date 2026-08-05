"""把现有应用、Memory、Claude 和 Codex resolver 投影为只读路径状态。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from trowel_py.application_paths import resolve_application_data_root
from trowel_py.configuration.models import PathEntry, PathStatus


def build_path_status(
    environment: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> PathStatus:
    """返回当前数据模式实际使用的配置和长期数据路径。

    Args:
        environment: 要解析的数据根、桌面模式和 Codex home 环境；省略时读取当前进程。
        home: Claude/Codex 默认目录使用的用户主目录；测试可传隔离路径。
    """

    source = os.environ if environment is None else environment
    user_home = Path.home() if home is None else home
    data_root = resolve_application_data_root(source, home=user_home)
    mode = source.get("TROWEL_DESKTOP_DATA_MODE", "").strip() or "browser"
    if mode not in {"packaged", "canonical-dev", "isolated-dev", "browser"}:
        mode = "browser"
    memory_root = data_root / "memory"
    codex_home = Path(source.get("CODEX_HOME", "").strip() or user_home / ".codex")
    paths = {
        "data_root": _entry(data_root, "directory"),
        "memory": _entry(memory_root, "directory"),
        "profile": _entry(memory_root / "profile.md", "file"),
        "trowel_config": _entry(data_root / "config.toml", "file"),
        "connection_registry": _entry(data_root / "trowel.db", "file"),
        "claude_settings": _entry(user_home / ".claude" / "settings.json", "file"),
        "codex_config": _entry(codex_home / "config.toml", "file"),
    }
    return PathStatus(mode, paths)


def _entry(path: Path, kind: str) -> PathEntry:
    """创建不读文件正文的路径存在状态。"""

    return PathEntry(path=path, exists=path.exists(), kind=kind)
