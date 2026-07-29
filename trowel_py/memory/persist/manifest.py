"""检查 completion manifest 引用的产物并恢复幂等跳过报告。"""

from pathlib import Path

from .models import PersistReport

_SEGMENTS_META_DIR = "meta/persisted-segments"


def _manifest_intact(root: Path, manifest: dict) -> bool:
    """检查 manifest 已声明的持久化产物是否仍存在。

    ``episode_file``、``reflection_file`` 和 ``escalation_file`` 仅在值为
    真时检查；上述三个路径字段缺省或为假值时视为未声明。
    ``notes_created`` 和 ``notes_updated`` 中的每个 Note ID 固定检查
    ``notes/<note_id>.md``。检查只调用 ``exists()``，不读取文件内容或
    frontmatter。本函数假设输入具有 ``persist_draft`` 写出的结构，不校验
    字段类型，因此畸形 manifest 可能抛出异常，而不是返回 ``False``。

    Args:
        root: manifest 中相对产物路径所基于的 Memory 根目录。
        manifest: ``persist_draft`` 写出的 completion manifest。

    Returns:
        所有已声明产物都存在时为 ``True``，任一产物缺失时为 ``False``。
    """
    episode = manifest.get("episode_file")
    if episode and not (root / episode).exists():
        return False
    note_ids = list(manifest.get("notes_created", [])) + list(
        manifest.get("notes_updated", [])
    )
    if any(not (root / "notes" / f"{note_id}.md").exists() for note_id in note_ids):
        return False
    reflection = manifest.get("reflection_file")
    if reflection and not (root / reflection).exists():
        return False
    escalation = manifest.get("escalation_file")
    return not escalation or (root / escalation).exists()


def _report_from_manifest(manifest: dict) -> PersistReport:
    """把已通过完整性检查的 manifest 转换为幂等跳过报告。

    ``notes_skipped`` 按 ``notes_created`` 后接 ``notes_updated`` 的顺序拼接，
    保留原顺序和重复项。``notes_written``、``diary_written`` 归零，
    ``verification_counts``、``notes_created`` 和 ``notes_updated`` 清空。
    ``episode_written`` 无条件为 ``True``；``reflection_written`` 和
    ``escalation_written`` 分别在对应字段不是 ``None`` 时为 ``True``，即使
    字段是空字符串。本函数不会再次执行完整性检查或写入文件。

    Args:
        manifest: 已由 ``_manifest_intact`` 确认产物存在的 completion manifest。

    Returns:
        ``ok=True`` 且指向原完成结果的 ``PersistReport``。

    Raises:
        KeyError: manifest 缺少构造路径所需的 ``segment_id``。
    """
    skipped = tuple(manifest.get("notes_created", [])) + tuple(
        manifest.get("notes_updated", [])
    )
    return PersistReport(
        notes_written=0,
        diary_written=0,
        verification_counts={},
        notes_created=(),
        notes_updated=(),
        notes_skipped=skipped,
        episode_written=True,
        reflection_written=manifest.get("reflection_file") is not None,
        escalation_written=manifest.get("escalation_file") is not None,
        manifest_path=(f"{_SEGMENTS_META_DIR}/{manifest['segment_id']}.json"),
        ok=True,
    )
