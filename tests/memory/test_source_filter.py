from __future__ import annotations

import json
from pathlib import Path

from trowel_py.memory.daily_review.source_refs import materialize_numbered_source
from trowel_py.memory.source_filter import (
    KERNEL_SOFT_YIELD_MARKER,
    is_kernel_control_record,
    materialize_memory_safe_source,
)


def _cc_user(text: str) -> bytes:
    return json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")


def test_only_prefixed_kernel_user_record_is_filtered() -> None:
    control = _cc_user(f"{KERNEL_SOFT_YIELD_MARKER}\ncontext_generation=2")
    discussion = _cc_user(f"代码里出现了 {KERNEL_SOFT_YIELD_MARKER} 标记")
    assistant = json.dumps(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": KERNEL_SOFT_YIELD_MARKER}
                ],
            },
        }
    ).encode()

    assert is_kernel_control_record(control) is True
    assert is_kernel_control_record(discussion) is False
    assert is_kernel_control_record(assistant) is False


def test_memory_sources_omit_control_without_mutating_offsets(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    user = _cc_user("保留这条真实用户消息")
    control = _cc_user(f"{KERNEL_SOFT_YIELD_MARKER}\ncontext_generation=0")
    raw = user + b"\n" + control + b"\n"
    source.write_bytes(raw)
    numbered_dir = tmp_path / "numbered"
    numbered_dir.mkdir()

    numbered = materialize_numbered_source(
        source,
        numbered_dir,
        start_offset=0,
        end_offset=len(raw),
    )
    safe = materialize_memory_safe_source(source, tmp_path)

    numbered_text = numbered.path.read_text(encoding="utf-8")
    safe_bytes = safe.read_bytes()
    assert "保留这条真实用户消息" in numbered_text
    assert KERNEL_SOFT_YIELD_MARKER not in numbered_text
    assert len(safe_bytes) == len(raw)
    assert KERNEL_SOFT_YIELD_MARKER.encode() not in safe_bytes
    assert source.read_bytes() == raw
