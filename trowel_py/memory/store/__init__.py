"""File-backed memory store 的稳定入口。"""

import logging

from .codec import (
    _ILLEGAL as _ILLEGAL,
    _NOTE_KEY_ORDER as _NOTE_KEY_ORDER,
    _WS_SLASH as _WS_SLASH,
    _coerce_meta_str as _coerce_meta_str,
    _core_item_from_dict as _core_item_from_dict,
    _diary_from_fm as _diary_from_fm,
    _dump_frontmatter as _dump_frontmatter,
    _matches as _matches,
    _note_from_fm as _note_from_fm,
    _ordered_note_frontmatter as _ordered_note_frontmatter,
    _safe_snapshot_name as _safe_snapshot_name,
    _slugify as _slugify,
    _split_frontmatter as _split_frontmatter,
)
from .core import _CORE_FILE as _CORE_FILE, _DICT_L0 as _DICT_L0
from .diary import _DIARY_DIR as _DIARY_DIR, _LAYER_DIR as _LAYER_DIR
from .episode_codec import (
    _DIARY_FIELDS as _DIARY_FIELDS,
    _SEG_END as _SEG_END,
    _SEG_START as _SEG_START,
    _episode_covers_date as _episode_covers_date,
    _extract_h2_block as _extract_h2_block,
    _h2_headings as _h2_headings,
    _parse_segment_blocks as _parse_segment_blocks,
    _parse_structured_block as _parse_structured_block,
    _render_date_block as _render_date_block,
    _render_segment as _render_segment,
    _segment_entry_for_date as _segment_entry_for_date,
    _single_line as _single_line,
)
from .episodes import _EPISODES_DIR as _EPISODES_DIR
from .notes import _NOTES_DIR as _NOTES_DIR
from .profile_io import _PROFILE_FILE as _PROFILE_FILE
from .repository import MemoryStore

logger = logging.getLogger(__name__)

__all__ = ["MemoryStore"]
