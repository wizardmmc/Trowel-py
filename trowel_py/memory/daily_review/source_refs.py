"""把 CompletedSegment 的精确 JSONL 范围变成可校验 line refs。"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NumberedSource:
    """记录提炼 host 读取的编号来源副本及草稿可用的引用。

    Attributes:
        path: 供提炼 host 读取的编号 JSONL 副本路径。
        refs: 草稿来源引用门禁允许使用的编号集合；副本没有保留记录时为空集。
    """

    path: Path
    refs: frozenset[str]


def materialize_numbered_source(
    source_path: Path,
    workdir: Path,
    *,
    start_offset: int | None,
    end_offset: int | None,
) -> NumberedSource:
    """过滤并编号 JSONL 半开字节区间，生成提炼使用的来源副本。

    函数不会校正落在记录中间的字节位置；调用方必须避免让范围边界切在 JSONL
    记录中间，否则截出的首尾残片也会参与过滤和编号。起点等于终点时生成空
    副本，终点超过文件末尾时按 EOF 截止。

    空白行和 Kernel 控制记录不会写入副本。其余记录按保留顺序从 ``L000001``
    重新编号，并以 ``Lxxxxxx<TAB>原记录`` 写入。输出文件名由源路径、规范化
    后的起点和传入的终点决定，源内容不参与命名；相同组合会通过临时文件原子
    覆盖同一路径。源文件保持不变。

    Args:
        source_path: 要截取的会话 JSONL 或 Codex 轮次日志。
        workdir: 已经存在的目录，用于存放编号副本和临时文件。
        start_offset: 半开字节区间的起点；为 None 时从文件开头读取。
        end_offset: 半开字节区间的终点；为 None 时读取到文件末尾。

    Returns:
        写入后的副本路径及其中全部合法引用。

    Raises:
        ValueError: 起点为负数，或终点早于起点。
        OSError: 无法读取源文件或写入、替换目标文件。
    """
    start = start_offset or 0
    if start < 0 or (end_offset is not None and end_offset < start):
        raise ValueError(
            f"invalid source byte range [{start}, {end_offset}]"
        )
    with source_path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read() if end_offset is None else handle.read(end_offset - start)
    lines = [line for line in raw.splitlines() if line.strip()]
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
