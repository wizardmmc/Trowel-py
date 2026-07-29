"""在调用方锁保护下换代 Dictionary L0/L1，并尽力恢复发布前的 L1。"""

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
    """换代 L0/L1，并在 L0 发布前失败时尝试恢复旧 L1。

    函数先把完整的新 L1 目录换入 live 路径，再用 ``os.replace`` 发布 L0。
    函数本身不加锁，调用方必须在这两步期间持有 Dictionary 排他锁，避免读者
    看到不同代的索引。L0 发布前失败时旧 L0 通常保持不变，函数会尝试恢复旧
    L1。恢复失败可能替代原始发布异常；临时 L0 的检查或删除异常也会向上传播，
    并在已有异常时替代它。

    Args:
        root: Dictionary 与内部暂存目录所在的 Memory 根目录。
        l0_text: 要发布的完整 L0 文本。
        l1_files: L1 文件名到完整文本的映射。键会原样拼接 ``.md`` 并用作
            文件路径；本函数不校验键，调用方必须保证键不是绝对路径且不含
            路径分隔符。

    Raises:
        Exception: 写入、换代、恢复或临时 L0 清理失败时向上传播；恢复或清理
            异常可能替代此前的异常。
    """
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
        # 暂存 L1 删除失败留给 TTL；临时 L0 的检查或删除异常会向上传播，
        # 并可能覆盖原异常。
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
    """尽力移走失败的新 L1，并把发布前目录恢复为 live。

    恢复旧目录的 ``rename`` 失败会向上传播，并可能替代调用方原本处理的发布
    异常。

    Args:
        live_l1: 当前 L1 的 live 路径。
        trash: 发布前 L1 暂存的回收路径。
        meta: 失败的新 L1 临时移入的目录。
        suffix: 本次发布使用的唯一后缀。
        swapped: 新 L1 是否已经换入 live 路径。
    """
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
    """尽力清理超过一小时的 Dictionary 暂存和回收目录。

    只处理 ``.dict-l1-staging-`` 和 ``.dict-l1-trash-`` 前缀的直接子目录；
    读取时间或删除失败时跳过，不阻止本次发布。

    Args:
        meta: Dictionary 发布工作目录所在的 ``meta`` 目录。
    """
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
