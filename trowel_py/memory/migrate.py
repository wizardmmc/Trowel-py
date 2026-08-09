"""为缺少 ``memory_id`` 的旧 Note 补齐身份和生命周期字段。

迁移只扫描 Memory 根目录下 ``notes/*.md``。每条待迁移 Note 获得新的 UUIDv7，
删除旧 ``retired`` 和 ``confidence`` 字段，并重建 ``status`` 与
``valid_from``。dry-run 也会生成 UUIDv7 并在内存中构建完整计划，但不创建
备份、不写回文件；apply 在改写任何 Note 前先把整个 Memory 根目录复制到同级
备份。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trowel_py.memory.ids import uuid7
from trowel_py.memory.store import _dump_frontmatter, _split_frontmatter

_NOTES_DIR = "notes"
# retired 会转换为 status；confidence 直接删除，已有 verification 保持不变。
_REMOVED_FIELDS = ("retired", "confidence")


@dataclass(frozen=True)
class MigrateReport:
    """汇总一次旧 Note 迁移的扫描、计划和备份结果。

    ``migrated`` 在 dry-run 中表示计划迁移数；apply 只有全部文件改写完成后才
    返回，此时它表示完成迁移数。写入异常时函数不会返回报告。

    Attributes:
        scanned: ``notes`` 目录下扫描到的顶层 ``.md`` 文件数，包括损坏或非 Note
            文件。
        migrated: ``memory_id`` 按 Python 布尔规则为假的目标 Note 数；dry-run
            表示计划数，apply 成功返回时表示完成数。
        skipped: ``memory_id`` 按 Python 布尔规则为真而跳过的目标 Note 数；
            损坏或 ``type`` 不是 ``note`` 的文件不计入。
        backed_up: apply 实际创建的备份目录；dry-run、无待迁移项或 notes 目录
            不存在时为 None。
    """

    scanned: int
    migrated: int
    skipped: int
    backed_up: str | None = None


def migrate_memory(root: Path | str, *, apply: bool) -> MigrateReport:
    """规划或执行旧 Note frontmatter 迁移。

    函数按文件名排序扫描顶层 ``notes/*.md``。无法解析 frontmatter 或 ``type``
    不是 ``note`` 的文件只计入扫描数。对 frontmatter 可解析为映射且 ``type``
    为 ``note`` 的文件，``memory_id`` 按 Python 布尔规则为真时计入跳过数，
    为假时纳入迁移。迁移会删除旧字段、生成新的 UUIDv7，按 ``retired`` 的
    Python 真值写入 ``active`` 或 ``retired`` 状态，并把 ``str(created)``
    写入 ``valid_from``；缺失 ``created`` 时写空字符串。其他 frontmatter
    字段和正文内容保留，但 YAML 会按当前序列化器重新格式化。

    dry-run 同样会生成 UUIDv7 并构建完整计划，只是不创建备份或写文件；因此
    UUID 生成和读取异常在两种模式下都会直接传播。

    apply 且存在待迁移项时，函数以当前 Unix 秒命名同级备份；同名目录已存在则
    从 ``-2`` 开始选择首个空闲后缀。备份成功后逐文件直接覆盖，不加锁、不做
    原子替换或失败回滚；中途写入失败可能留下完整备份和部分已迁移文件。读取、
    UUID 生成、备份或写入异常均直接传播。

    Args:
        root: 要迁移的 Memory 根目录。
        apply: True 时备份并写入；False 时只生成迁移计划和报告。

    Returns:
        扫描、计划迁移、跳过数量及可选备份路径。
    """
    root_path = Path(root)
    notes_dir = root_path / _NOTES_DIR
    if not notes_dir.exists():
        return MigrateReport(scanned=0, migrated=0, skipped=0, backed_up=None)

    # 先在内存中生成含 UUID 的完整计划，使两种模式共用筛选和计数。
    plans: list[tuple[Path, dict[str, Any], str]] = []
    scanned = 0
    skipped = 0
    for p in sorted(notes_dir.glob("*.md")):
        scanned += 1
        text = p.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text)
        if fm is None or fm.get("type") != "note":
            # 损坏或非 Note 文件计入 scanned，但不计入 skipped 或 migrated。
            continue
        if fm.get("memory_id"):
            skipped += 1
            continue
        new_fm = {k: v for k, v in fm.items() if k not in _REMOVED_FIELDS}
        new_fm["memory_id"] = str(uuid7())
        # status 始终按旧 retired 的真值重建，覆盖可能已有的 status。
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

    # 同秒已有备份时递增后缀，避免覆盖先前备份。
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
