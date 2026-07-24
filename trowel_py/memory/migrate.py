"""为旧 note 补齐稳定身份和生命周期字段的一次性迁移。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from trowel_py.memory.ids import uuid7
from trowel_py.memory.store import _dump_frontmatter, _split_frontmatter

_NOTES_DIR = "notes"
# status 取代 retired，verification 取代 confidence。
_REMOVED_FIELDS = ("retired", "confidence")


@dataclass(frozen=True)
class MigrateReport:
    scanned: int
    migrated: int
    skipped: int
    backed_up: str | None = None


def migrate_memory(root: Path | str, *, apply: bool) -> MigrateReport:
    """dry-run 只报告计划；apply 在原地写入前备份整个 memory root。"""
    root_path = Path(root)
    notes_dir = root_path / _NOTES_DIR
    if not notes_dir.exists():
        return MigrateReport(scanned=0, migrated=0, skipped=0, backed_up=None)

    # 先完成无写入计划，使 dry-run 与 apply 共用读取和计数路径。
    plans: list[tuple[Path, dict, str]] = []
    scanned = 0
    skipped = 0
    for p in sorted(notes_dir.glob("*.md")):
        scanned += 1
        text = p.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text)
        if fm is None or fm.get("type") != "note":
            continue  # 损坏或非 note 文件不由本迁移修复。
        if fm.get("memory_id"):
            skipped += 1
            continue
        new_fm = {k: v for k, v in fm.items() if k not in _REMOVED_FIELDS}
        new_fm["memory_id"] = str(uuid7())
        # 旧 retired 字段必须先于字段删除解释。
        if fm.get("retired"):
            new_fm["status"] = "retired"
        else:
            new_fm["status"] = "active"
        new_fm["valid_from"] = str(fm.get("created", ""))
        plans.append((p, new_fm, body))

    if not apply or not plans:
        return MigrateReport(
            scanned=scanned, migrated=len(plans), skipped=skipped, backed_up=None
        )

    # 同秒已有备份时递增后缀，既不覆盖旧备份也不中断本次迁移。
    import time

    base = root_path.with_name(root_path.name + f".bak-migrate-{int(time.time())}")
    backup = base
    i = 2
    while backup.exists():
        backup = base.with_name(f"{base.name}-{i}")
        i += 1
    shutil.copytree(root_path, backup)
    for p, new_fm, body in plans:
        p.write_text(_dump_frontmatter(new_fm, body), encoding="utf-8")
    return MigrateReport(
        scanned=scanned, migrated=len(plans), skipped=skipped, backed_up=str(backup)
    )
