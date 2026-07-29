"""提供提炼草稿的稳定持久化入口和结果类型。

稳定 API 仅包括 ``persist_draft`` 和 ``PersistReport``；下划线名称属于内部
实现，不应由调用方依赖。
"""

from .artifacts import _write_meta as _write_meta
from .manifest import (
    _SEGMENTS_META_DIR as _SEGMENTS_META_DIR,
    _manifest_intact as _manifest_intact,
    _report_from_manifest as _report_from_manifest,
)
from .models import PersistReport
from .notes import (
    _content_hash as _content_hash,
    _update_note as _update_note,
    _write_new_note as _write_new_note,
)
from .run import persist_draft

__all__ = ["PersistReport", "persist_draft"]
