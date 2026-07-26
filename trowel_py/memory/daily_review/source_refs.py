"""把 CompletedSegment 的精确 JSONL 范围变成可校验 line refs。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from trowel_py.memory.source_filter import is_kernel_control_record


@dataclass(frozen=True)
class NumberedSource:
    path: Path
    refs: frozenset[str]


def materialize_numbered_source(
    source_path: Path,
    workdir: Path,
    *,
    start_offset: int | None,
    end_offset: int | None,
) -> NumberedSource:
    start = start_offset or 0
    if start < 0 or (end_offset is not None and end_offset < start):
        raise ValueError(
            f"invalid source byte range [{start}, {end_offset}]"
        )
    with source_path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read() if end_offset is None else handle.read(end_offset - start)
    lines = [
        line
        for line in raw.splitlines()
        if line.strip() and not is_kernel_control_record(line)
    ]
    refs = tuple(f"L{index:06d}" for index in range(1, len(lines) + 1))
    rendered = b"".join(
        ref.encode("ascii") + b"\t" + line + b"\n"
        for ref, line in zip(refs, lines, strict=True)
    )
    identity = hashlib.sha256(
        f"{source_path}:{start}:{end_offset}".encode("utf-8")
    ).hexdigest()[:16]
    path = workdir / f"source-{identity}.numbered.jsonl"
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_bytes(rendered)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return NumberedSource(path=path, refs=frozenset(refs))
