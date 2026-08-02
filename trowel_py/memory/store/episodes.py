"""持久化来源 Episode，并把其中的经历投影到 Daily。"""

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
    _entry_from_episode_meta,
    _extract_h2_block,
    _h2_headings,
    _parse_segment_blocks,
    _parse_structured_block,
    _render_segment,
    _segment_entry_for_date,
)

_EPISODES_DIR = "episodes"


class _EpisodeStore(_DiaryStore):
    """为 ``MemoryStore`` 提供 Episode 写入、每日投影和归因审计能力。

    组合后的仓储必须提供 ``root``。无效或非映射的 frontmatter 在投影与审计
    时跳过，写入时按空元数据处理；I/O、UTF-8、YAML 序列化和其余元数据类型
    错误直接传播。
    """

    root: Path

    def write_episode(
        self, context: PersistContext, diary_entries: tuple[DraftDiary, ...]
    ) -> str:
        """在会话 Episode 文件中按 ``segment_id`` 新增或替换来源片段。

        已有文件中边界完整的其他 segment 及其元数据会保留；同 ID 的块在原
        顺序位置替换。marker 外文本和无法配对的块不会写回。顶层活动日期合并
        旧值与本次 Diary 日期，其他顶层会话字段使用当前 context 重建；未提供
        ``completed_segment`` 时保留已有 host 身份字段。结构化 items、来源
        模型和派生信息会写入片段元数据。

        目标路径直接拼接 ``cc_session_id``，不清理绝对路径、父目录或路径
        分隔符。内容先写入目标文件同目录的固定 ``.tmp`` 路径，再原子替换
        正式文件；失败时清理临时文件。

        Args:
            context: 当前来源片段的会话、字节范围、日期和 provenance。
            diary_entries: 要写入该片段的经历；空元组会生成 empty reason。

        Returns:
            ``context.cc_session_id``。
        """

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
        structured_entries = [entry for entry in diary_entries if entry.items]
        if structured_entries:
            seg_meta["episode_schema_version"] = 3
            seg_meta["episode_items"] = [
                {"date": entry.date, "item": episode_item_to_dict(item)}
                for entry in structured_entries
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
        """聚合目标日期的 Episode 正文并覆盖对应 Daily Diary。

        投影按 Episode 登记时间排序，各段以空行分隔。没有可投影内容时返回空
        字符串且不改动已有 Daily；成功时写入最小 day-layer frontmatter，并
        返回输入日期。
        """

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
        """投影目标日期的 Episode Markdown 正文。

        每个可配对 segment 优先按非空 ``activity_dates`` 路由；该字段缺失或
        为空时才按二级日期标题路由。文件没有可配对 segment 时优先按二级标题
        提取；连二级标题也没有时，按顶层非空 ``activity_dates`` 路由，只有该
        字段缺失或为空才回退 ``review_date``。一旦存在可配对 segment 或任意
        二级标题，不再走整文件回退。无论采用哪种 segment 路由，最终都必须
        找到目标日期的二级标题和非空正文；因此结构化投影也只会在对应 Markdown
        日期块存在时尝试恢复结构化元数据。

        缺失、空或无法解析为映射的 frontmatter 会被跳过；不校验顶层
        ``type``。结果为 ``(registered_at, 正文)``，按登记时间排序，同时间
        保持文件路径和 segment 顺序。
        """

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
        """投影目标日期的结构化经历，并保留来源身份。

        日期路由和旧格式回退顺序与 ``project_daily_entries()`` 相同。segment
        优先从结构化元数据恢复，失败时解析 Markdown 的旧四类列表或自由文本；
        来源 ID 使用 ``segment_id``。无 segment 的旧文件改用顶层
        ``cc_session_id``。

        Returns:
            ``(来源 ID, registered_at, DraftDiary)`` 列表，按登记时间排序；
            同时间保持文件路径和 segment 顺序。
        """

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
                        structured = _entry_from_episode_meta(
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
        """统计可读取文件是否包含非空的片段级活动日期。

        缺失、空或无法解析为映射的 frontmatter 会被跳过，顶层 ``type`` 不
        校验。只要任一映射型 segment 的 ``activity_dates`` 为真，就计入
        ``with_segment_dates``，否则计入 ``legacy``。

        Returns:
            包含 ``episodes``、``with_segment_dates`` 和 ``legacy`` 的计数。
        """

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
