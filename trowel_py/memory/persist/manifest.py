"""Completion manifest 的完整性门禁与报告恢复。"""

from pathlib import Path

from .models import PersistReport

_SEGMENTS_META_DIR = "meta/persisted-segments"


def _manifest_intact(root: Path, manifest: dict) -> bool:
    """只有 manifest 声称的全部产物仍存在时才允许跳过重跑。"""
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
