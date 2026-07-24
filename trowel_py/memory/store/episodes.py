"""Episode segment 的写入与 daily 投影。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from trowel_py.memory.draft import DraftDiary, episode_item_to_dict
from trowel_py.memory.provenance import (
    derivation_to_dict,
    model_identity_to_dict,
    segment_source_to_dict,
)
from trowel_py.memory.types import PersistContext

from .codec import _coerce_meta_str, _dump_frontmatter, _split_frontmatter
from .diary import _DiaryStore
from .episode_codec import (
    _episode_covers_date,
    _entry_from_v2_meta,
    _extract_h2_block,
    _h2_headings,
    _parse_segment_blocks,
    _parse_structured_block,
    _render_segment,
    _segment_entry_for_date,
)

_EPISODES_DIR = "episodes"


class _EpisodeStore(_DiaryStore):
    root: Path

    def write_episode(
        self, context: PersistContext, diary_entries: tuple[DraftDiary, ...]
    ) -> str:
        """按 segment_id 原位 upsert，并保留同 session 的其他 segment。"""

        path = self.root / _EPISODES_DIR / f"{context.cc_session_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
            fm = fm or {}
            blocks = _parse_segment_blocks(body)
            segs = list(fm.get("segments") or [])
            prior_dates = set(fm.get("activity_dates") or [])
        else:
            fm, body = {}, ""
            blocks = _parse_segment_blocks(body)
            segs = []
            prior_dates = set()

        block_text, content_hash, dates, empty_reason = _render_segment(
            context.segment_id, diary_entries
        )

        blocks[context.segment_id] = block_text

        seg_meta: dict[str, Any] = {
            "segment_id": context.segment_id,
            "start_offset": context.source_start_offset,
            "end_offset": context.source_end_offset,
            "review_date": context.review_date,
            "content_hash": content_hash,
            "activity_dates": list(context.activity_dates),
            "date_basis": context.date_basis,
            "processed_date": context.processed_date,
        }
        if empty_reason:
            seg_meta["empty_reason"] = empty_reason
        if context.completed_segment is not None:
            seg_meta["source"] = segment_source_to_dict(
                context.completed_segment.source
            )
            seg_meta["source_models"] = [
                model_identity_to_dict(identity)
                for identity in context.completed_segment.source_models
            ]
        if context.derivation is not None:
            seg_meta["derivation"] = derivation_to_dict(context.derivation)
        v2_entries = [entry for entry in diary_entries if entry.items]
        if v2_entries:
            seg_meta["episode_schema_version"] = 2
            seg_meta["source_ref_scheme"] = "nonempty_jsonl_line_v1"
            seg_meta["episode_items"] = [
                {"date": entry.date, "item": episode_item_to_dict(item)}
                for entry in v2_entries
                for item in entry.items
            ]
        new_segs: list[dict[str, Any]] = []
        replaced = False
        for s in segs:
            if s.get("segment_id") == context.segment_id:
                new_segs.append(seg_meta)
                replaced = True
            else:
                new_segs.append(s)
        if not replaced:
            new_segs.append(seg_meta)

        fm_out: dict[str, Any] = {
            "type": "episode",
            "cc_session_id": context.cc_session_id,
            "workdir": context.workdir,
            "registered_at": context.registered_at,
            "review_date": context.review_date,
            "activity_dates": sorted(prior_dates | set(dates)),
            "source_jsonl": context.source_jsonl,
            "segments": new_segs,
        }
        if context.completed_segment is not None:
            fm_out["host_kind"] = context.completed_segment.host_kind
            fm_out["native_session_id"] = context.completed_segment.native_session_id
            fm_out["trowel_session_ids"] = list(
                context.completed_segment.trowel_session_ids
            )
        else:
            for key in ("host_kind", "native_session_id", "trowel_session_ids"):
                if key in fm:
                    fm_out[key] = fm[key]
        body_out = "".join(blocks.values())
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            temporary.write_text(
                _dump_frontmatter(fm_out, body_out),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return context.cc_session_id

    def derive_daily_from_episodes(self, date: str) -> str:

        items = self.project_daily_entries(date)
        if not items:
            return ""
        daily_body = "\n\n".join(body for _ts, body in items)
        self.write_diary(
            {
                "type": "diary",
                "date": date,
                "layer": "day",
                "period": date,
                "promoted_knowledge": [],
                "__body": daily_body,
            }
        )
        return date

    def project_daily_entries(self, date: str) -> list[tuple[str, str]]:
        """优先使用 segment 日期；旧文件按标题与顶层日期保守回退。"""

        eps_dir = self.root / _EPISODES_DIR
        if not eps_dir.exists():
            return []
        items: list[tuple[str, str]] = []
        for p in sorted(eps_dir.glob("*.md")):
            fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
            if not fm:
                continue
            registered_at = _coerce_meta_str(fm.get("registered_at"))
            seg_metas = {
                s.get("segment_id"): s
                for s in (fm.get("segments") or [])
                if isinstance(s, dict)
            }
            blocks = _parse_segment_blocks(body)
            if blocks:
                for seg_id, block in blocks.items():
                    entry = _segment_entry_for_date(
                        block, date, seg_metas.get(seg_id, {})
                    )
                    if entry:
                        items.append((registered_at, entry))
            elif _h2_headings(body):
                entry = _extract_h2_block(body, date)
                if entry:
                    items.append((registered_at, entry))
            elif _episode_covers_date(fm, date):
                stripped = body.strip()
                if stripped:
                    items.append((registered_at, stripped))
        items.sort(key=lambda x: x[0])
        return items

    def project_daily_sources(self, date: str) -> list[tuple[str, str, DraftDiary]]:

        eps_dir = self.root / _EPISODES_DIR
        if not eps_dir.exists():
            return []
        out: list[tuple[str, str, DraftDiary]] = []
        for p in sorted(eps_dir.glob("*.md")):
            fm, body = _split_frontmatter(p.read_text(encoding="utf-8"))
            if not fm:
                continue
            registered_at = _coerce_meta_str(fm.get("registered_at"))
            cc_id = _coerce_meta_str(fm.get("cc_session_id"))
            seg_metas = {
                s.get("segment_id"): s
                for s in (fm.get("segments") or [])
                if isinstance(s, dict)
            }
            blocks = _parse_segment_blocks(body)
            if blocks:
                for seg_id, block in blocks.items():
                    entry_block = _segment_entry_for_date(
                        block, date, seg_metas.get(seg_id, {})
                    )
                    if entry_block:
                        structured = _entry_from_v2_meta(
                            seg_metas.get(seg_id, {}), date
                        )
                        out.append(
                            (
                                seg_id,
                                registered_at,
                                structured
                                or _parse_structured_block(entry_block, date),
                            )
                        )
            elif _h2_headings(body):
                entry_block = _extract_h2_block(body, date)
                if entry_block:
                    out.append(
                        (
                            cc_id,
                            registered_at,
                            _parse_structured_block(entry_block, date),
                        )
                    )
            elif _episode_covers_date(fm, date):
                stripped = body.strip()
                if stripped:
                    out.append(
                        (cc_id, registered_at, _parse_structured_block(stripped, date))
                    )
        out.sort(key=lambda x: x[1])
        return out

    def audit_episode_attribution(self) -> dict[str, Any]:

        eps_dir = self.root / _EPISODES_DIR
        report: dict[str, Any] = {
            "episodes": 0,
            "with_segment_dates": 0,
            "legacy": 0,
        }
        if not eps_dir.exists():
            return report
        for p in sorted(eps_dir.glob("*.md")):
            fm, _body = _split_frontmatter(p.read_text(encoding="utf-8"))
            if not fm:
                continue
            report["episodes"] += 1
            segs = [s for s in (fm.get("segments") or []) if isinstance(s, dict)]
            if any(s.get("activity_dates") for s in segs):
                report["with_segment_dates"] += 1
            else:
                report["legacy"] += 1
        return report
