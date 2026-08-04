"""通过 Memory 公开门面读取指定本地根目录的统计事实。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from trowel_py.memory import access_log as access_log_module
from trowel_py.memory.access_log import AccessRecord, OutcomeRecord
from trowel_py.memory import judgements as judgements_module
from trowel_py.memory.judgements import JudgementReport
from trowel_py.memory.statistics_facade import read_memory_statistics
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Note, NoteId
from trowel_py.statistics.window import StatisticsWindow


@dataclass(frozen=True)
class _FileVersion:
    """标识一份统计来源文件的内容是否可能变化。

    Attributes:
        name: 相对当前来源根的文件名。
        device: 文件所在设备编号。
        inode: 当前目录项指向的文件编号。
        size: 文件字节数。
        modified_ns: 文件内容最近修改时间的纳秒值。
    """

    name: str
    device: int
    inode: int
    size: int
    modified_ns: int


class _NoteSnapshotCache:
    """按文件版本复用完整 Note 快照，并在任何 Note 变化后失效。"""

    def __init__(self, root: Path) -> None:
        """保存 Memory 根并初始化空快照。

        Args:
            root: 包含 ``notes`` 子目录的 Memory 根目录。
        """

        self._root = root
        self._version: tuple[_FileVersion, ...] | None = None
        self._rows: list[tuple[NoteId, Note]] = []
        self._lock = Lock()

    def read(self) -> list[tuple[NoteId, Note]]:
        """返回当前 Note 快照；目录内容变化时重新完整加载。

        Returns:
            文件 stem 与 Note 组成的路径顺序快照副本。
        """

        with self._lock:
            version = self._read_version()
            if version != self._version:
                self._rows = MemoryStore(self._root).load_notes_with_id()
                # 记录加载开始前的版本。若文件在加载期间变化，下一次读取会发现
                # 当前版本不同并重载，不能把可能混合新旧内容的快照标成最新版。
                self._version = version
            return list(self._rows)

    def _read_version(self) -> tuple[_FileVersion, ...]:
        """读取每份 Note 的稳定文件元数据，不打开或解析正文。"""

        notes_dir = self._root / "notes"
        if not notes_dir.is_dir():
            return ()
        versions: list[_FileVersion] = []
        for path in sorted(notes_dir.glob("*.md")):
            try:
                stat = path.stat()
            except FileNotFoundError:
                # 并发重命名会让本轮版本与加载后的复核不同，下一次自动重读。
                continue
            versions.append(
                _FileVersion(
                    name=path.name,
                    device=stat.st_dev,
                    inode=stat.st_ino,
                    size=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                )
            )
        return tuple(versions)


@dataclass(frozen=True)
class _EvidenceSnapshot:
    """保存一次 Memory 统计请求复用的三类原始证据。

    Attributes:
        access_records: 已解析的搜索、命中和读取事件。
        outcome_records: 已解析的读取反馈事件。
        judgement_reports: 已解析并完成新旧分段去重的判效报告。
    """

    access_records: list[AccessRecord]
    outcome_records: list[OutcomeRecord]
    judgement_reports: list[JudgementReport]


class _EvidenceSnapshotCache:
    """分别按来源文件版本复用 Memory 原始证据，避免同请求和跨范围重读。"""

    def __init__(self, root: Path) -> None:
        """保存 Memory 根并初始化三类空快照。

        Args:
            root: 包含 ``meta`` 原始日志与判效报告的 Memory 根目录。
        """

        self._root = root
        self._access_version: tuple[_FileVersion, ...] | None = None
        self._outcome_version: tuple[_FileVersion, ...] | None = None
        self._judgement_version: tuple[_FileVersion, ...] | None = None
        self._access_records: list[AccessRecord] = []
        self._outcome_records: list[OutcomeRecord] = []
        self._judgement_reports: list[JudgementReport] = []
        self._lock = Lock()

    def read(self) -> _EvidenceSnapshot:
        """返回当前证据快照，只重读版本发生变化的来源。

        Returns:
            三类已解析证据的列表副本。
        """

        with self._lock:
            access_version = self._single_file_version("access-log.jsonl")
            if access_version != self._access_version:
                self._access_records = access_log_module.read_access_log(self._root)
                self._access_version = access_version

            outcome_version = self._single_file_version("outcome-log.jsonl")
            if outcome_version != self._outcome_version:
                self._outcome_records = access_log_module.read_outcome_log(self._root)
                self._outcome_version = outcome_version

            judgement_version = self._judgement_files_version()
            if judgement_version != self._judgement_version:
                self._judgement_reports = (
                    judgements_module.load_all_judgement_reports(self._root)
                )
                self._judgement_version = judgement_version

            return _EvidenceSnapshot(
                access_records=list(self._access_records),
                outcome_records=list(self._outcome_records),
                judgement_reports=list(self._judgement_reports),
            )

    def _single_file_version(self, name: str) -> tuple[_FileVersion, ...]:
        """返回 ``meta`` 下单个日志文件的零或一项版本。"""

        path = self._root / "meta" / name
        version = _file_version(path, name)
        return () if version is None else (version,)

    def _judgement_files_version(self) -> tuple[_FileVersion, ...]:
        """返回全部分层判效 JSON 的路径顺序版本。"""

        directory = self._root / "meta" / "judgements"
        if not directory.is_dir():
            return ()
        versions = [
            version
            for path in sorted(directory.rglob("*.json"))
            if (
                version := _file_version(
                    path,
                    str(path.relative_to(directory)),
                )
            )
            is not None
        ]
        return tuple(versions)


def _file_version(path: Path, name: str) -> _FileVersion | None:
    """读取单个文件版本；并发消失或不可访问时返回 None。

    Args:
        path: 要检查但不解析的文件路径。
        name: 写入版本键的稳定相对名称。

    Returns:
        文件存在且可读取元数据时的版本，否则为 None。
    """

    try:
        stat = path.stat()
    except OSError:
        return None
    return _FileVersion(
        name=name,
        device=stat.st_dev,
        inode=stat.st_ino,
        size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
    )


class FileMemoryStatisticsReader:
    """读取一个 Memory 根目录，不持有数据库连接或写入水位。

    Attributes:
        root: Note、访问日志、判效报告和 Dictionary 状态所在的 Memory 根目录。
    """

    def __init__(
        self,
        root: Path | str,
        *,
        strict_read_only: bool = False,
    ) -> None:
        """保存后续查询使用的 Memory 根目录。

        Args:
            root: 要读取的隔离或正式 Memory 根目录。
            strict_read_only: 是否禁止 sessions.db schema 迁移。
        """
        self.root = Path(root)
        self.strict_read_only = strict_read_only
        self._notes = _NoteSnapshotCache(self.root)
        self._evidence = _EvidenceSnapshotCache(self.root)

    def read(self, window: StatisticsWindow) -> dict[str, Any]:
        """读取查询窗内的 Memory 使用事实和当前资产快照。

        Args:
            window: 已按调用方时区解析的半开时间窗。

        Returns:
            Memory 公开门面生成的不含正文和身份标识的快照。
        """
        evidence = self._evidence.read()
        return read_memory_statistics(
            self.root,
            window_start=window.start,
            window_end=window.end,
            local_tz=window.start.tzinfo,
            strict_read_only=self.strict_read_only,
            notes_with_id=self._notes.read(),
            access_records=evidence.access_records,
            outcome_records=evidence.outcome_records,
            judgement_reports=evidence.judgement_reports,
        )
