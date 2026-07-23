"""原子发布同一代 dictionary L0/L1，并在失败时恢复旧索引。"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from pathlib import Path

_DICTIONARY_L0 = "dictionary-L0.md"
_DICTIONARY_L1_DIR = "dictionary-L1"
_STALE_DIRECTORY_TTL = 3600


def atomic_replace(
    root: Path,
    l0_text: str,
    l1_files: dict[str, str],
) -> None:
    meta = root / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    _remove_stale_work_directories(meta)

    suffix = uuid.uuid4().hex[:8]
    staging_l1 = meta / f".dict-l1-staging-{suffix}"
    trash = meta / f".dict-l1-trash-{suffix}"
    live_l1 = root / _DICTIONARY_L1_DIR
    l0_path = root / _DICTIONARY_L0
    temporary_l0 = l0_path.with_name(f"{l0_path.name}.tmp-{suffix}")
    swapped = False
    try:
        staging_l1.mkdir(parents=True, exist_ok=True)
        for name, text in l1_files.items():
            (staging_l1 / f"{name}.md").write_text(
                text,
                encoding="utf-8",
            )
        if live_l1.exists():
            live_l1.rename(trash)
        staging_l1.rename(live_l1)
        swapped = True
        temporary_l0.write_text(l0_text, encoding="utf-8")
        os.replace(temporary_l0, l0_path)
    except Exception:
        _restore_previous_l1(
            live_l1=live_l1,
            trash=trash,
            meta=meta,
            suffix=suffix,
            swapped=swapped,
        )
        raise
    finally:
        # 清理失败不能掩盖原异常；残留目录会由后续 TTL 清理收口。
        shutil.rmtree(staging_l1, ignore_errors=True)
        if temporary_l0.exists():
            temporary_l0.unlink(missing_ok=True)
    if trash.exists():
        shutil.rmtree(trash, ignore_errors=True)


def _restore_previous_l1(
    *,
    live_l1: Path,
    trash: Path,
    meta: Path,
    suffix: str,
    swapped: bool,
) -> None:
    if swapped:
        # 先原子让出 live 路径，避免局部清理失败阻塞旧目录恢复。
        if live_l1.exists():
            aside = meta / f".dict-l1-failed-{suffix}"
            try:
                live_l1.rename(aside)
                shutil.rmtree(aside, ignore_errors=True)
            except OSError:
                shutil.rmtree(live_l1, ignore_errors=True)
        if trash.exists() and not live_l1.exists():
            trash.rename(live_l1)
    elif trash.exists() and not live_l1.exists():
        trash.rename(live_l1)


def _remove_stale_work_directories(meta: Path) -> None:
    if not meta.exists():
        return
    now = time.time()
    for path in meta.iterdir():
        if not (
            path.name.startswith(".dict-l1-staging-")
            or path.name.startswith(".dict-l1-trash-")
        ):
            continue
        try:
            if now - path.stat().st_mtime > _STALE_DIRECTORY_TTL:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
