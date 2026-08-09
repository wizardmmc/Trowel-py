"""管理 Codex 连接独占的配置家与两处全局用户配置的覆盖式继承。"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from trowel_py.application_paths import resolve_application_data_root

_INHERITED_MARKER = ".trowel-inherited.json"
_SUPPORTED_CODEX_DIRECTORIES = ("rules", "skills")
_SUPPORTED_CODEX_FILES = ("AGENTS.md", "config.toml")
_MANAGED_PATHS = (
    *(Path(name) for name in _SUPPORTED_CODEX_DIRECTORIES),
    *(Path(name) for name in _SUPPORTED_CODEX_FILES),
    Path(".agents") / "skills",
    Path(_INHERITED_MARKER),
)
_ROOT_LOCKS: dict[Path, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class CodexConnectionHomeError(RuntimeError):
    """表示 Codex 连接家路径、继承来源或原子发布不安全。"""


@dataclass(frozen=True)
class CodexInheritanceResult:
    """返回一次 Codex 配置继承完成后的脱敏事实。

    Attributes:
        inherited: 本次继承是否完整发布并写入成功标记。
        copied_entries: 从 ``~/.codex`` 复制的顶层配置名称。
        agents_skill_count: 从 ``~/.agents/skills`` 复制的 skill 数量。
    """

    inherited: bool
    copied_entries: tuple[str, ...]
    agents_skill_count: int


class CodexConnectionHomeStore:
    """为每项 Codex 连接分配独立配置家，并执行显式一次性继承。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        global_codex_home: Path | None = None,
        global_agents_home: Path | None = None,
    ) -> None:
        """保存托管根与两处恒定的真实全局继承来源。

        Args:
            root: Trowel 托管 Codex 连接家的父目录；None 使用应用数据根。
            global_codex_home: Codex 原生全局配置家；None 使用 ``~/.codex``。
            global_agents_home: 跨 Agent 用户配置根；None 使用 ``~/.agents``。
        """

        self.root = (
            root
            if root is not None
            else resolve_application_data_root() / "codex-accounts"
        ).expanduser().absolute()
        self.global_codex_home = (
            global_codex_home
            if global_codex_home is not None
            else Path.home() / ".codex"
        ).expanduser().absolute()
        self.global_agents_home = (
            global_agents_home
            if global_agents_home is not None
            else Path.home() / ".agents"
        ).expanduser().absolute()
        with _ROOT_LOCKS_GUARD:
            self._lock = _ROOT_LOCKS.setdefault(self.root, threading.RLock())

    def home_for(self, connection_id: str) -> Path:
        """按连接 UUID 返回不会跟随符号链接的托管配置家路径。

        Args:
            connection_id: 设置域分配的稳定连接 UUID。

        Returns:
            托管根下以规范 UUID 命名的 Codex 配置家。

        Raises:
            CodexConnectionHomeError: 连接 ID 无效或槽位是符号链接。
        """

        try:
            normalized = str(uuid.UUID(connection_id))
        except ValueError as exc:
            raise CodexConnectionHomeError("Codex 连接家标识无效") from exc
        home = self.root / normalized
        if home.is_symlink():
            raise CodexConnectionHomeError("Codex 连接家不能是符号链接")
        return home

    def ensure_home(self, connection_id: str) -> Path:
        """惰性创建仅当前账号可访问的 Codex 配置家并返回路径。"""

        with self._lock:
            self._ensure_root()
            home = self.home_for(connection_id)
            self._recover_interrupted_publish(connection_id, home)
            if home.exists():
                if not home.is_dir() or home.is_symlink():
                    raise CodexConnectionHomeError("Codex 连接家路径不安全")
            else:
                home.mkdir(mode=0o700)
            home.chmod(0o700)
            return home

    def existing_home(self, connection_id: str) -> Path | None:
        """返回现存且安全的连接家；尚未创建时返回 None。

        Args:
            connection_id: 设置域分配的稳定连接 UUID。

        Returns:
            可由 Trowel 精确删除或移动的连接家，或尚不存在时的 None。

        Raises:
            CodexConnectionHomeError: 现存入口不是普通私有目录。
        """

        with self._lock:
            self._ensure_root()
            home = self.home_for(connection_id)
            self._recover_interrupted_publish(connection_id, home)
            if not home.exists():
                return None
            if not home.is_dir() or home.is_symlink():
                raise CodexConnectionHomeError("Codex 连接家路径不安全")
            return home

    def is_inherited(self, connection_id: str) -> bool:
        """判断连接家是否具有一次完整继承成功后的标记。"""

        with self._lock:
            self._ensure_root()
            home = self.home_for(connection_id)
            self._recover_interrupted_publish(connection_id, home)
            marker = home / _INHERITED_MARKER
            return marker.is_file() and not marker.is_symlink()

    def inherit_global(
        self,
        connection_id: str,
        *,
        forbidden_values: Iterable[str] = (),
    ) -> CodexInheritanceResult:
        """覆盖复制 Codex 用户配置，同时保留连接自己的认证和运行状态。

        ``~/.codex`` 只复制 ``config.toml``、``AGENTS.md``、``rules`` 和
        兼容 ``skills``；``~/.agents`` 只复制 ``skills``。认证文件、会话、
        SQLite、日志、插件物理缓存和其他运行状态不会进入连接家。源中的符号链接
        会按当前目标物化，发布后各连接独立演化。

        Args:
            connection_id: 接收全局配置副本的 Codex 连接 ID。
            forbidden_values: Trowel 已知凭据原值；发布前命中任一值就拒绝继承。

        Returns:
            不含配置正文和本机路径的继承结果。

        Raises:
            CodexConnectionHomeError: 来源损坏、路径不安全或发布失败。
        """

        with self._lock:
            home = self.ensure_home(connection_id)
            transaction_id = uuid.uuid4().hex
            overlay = self.root / f".inherit-overlay-{connection_id}-{transaction_id}"
            replacement = self.root / f".inherit-stage-{connection_id}-{transaction_id}"
            backup = self.root / f".inherit-backup-{connection_id}-{transaction_id}"
            overlay.mkdir(mode=0o700)
            copied_entries: list[str] = []
            agents_skill_count = 0
            try:
                for name in _SUPPORTED_CODEX_DIRECTORIES:
                    source = self.global_codex_home / name
                    if not source.exists() and not source.is_symlink():
                        continue
                    self._copy_directory(source, overlay / name, label=name)
                    copied_entries.append(name)
                for name in _SUPPORTED_CODEX_FILES:
                    source = self.global_codex_home / name
                    if not source.exists() and not source.is_symlink():
                        continue
                    self._copy_regular_file(source, overlay / name, label=name)
                    copied_entries.append(name)
                agents_skills = self.global_agents_home / "skills"
                if agents_skills.exists() or agents_skills.is_symlink():
                    target = overlay / ".agents" / "skills"
                    target.parent.mkdir(mode=0o700)
                    self._copy_directory(
                        agents_skills,
                        target,
                        label=".agents/skills",
                    )
                    agents_skill_count = sum(
                        1
                        for path in target.rglob("SKILL.md")
                        if path.is_file() and not path.is_symlink()
                    )
                self._assert_forbidden_values_absent(overlay, forbidden_values)
                self._write_private_json(
                    overlay / _INHERITED_MARKER,
                    {
                        "inherited_at": datetime.now(UTC).isoformat(),
                        "sources": ["global_codex_home", "global_agents_home"],
                    },
                )
                self._publish(home, overlay, replacement, backup)
            except CodexConnectionHomeError:
                raise
            except (OSError, ValueError, TypeError) as exc:
                raise CodexConnectionHomeError("继承全局 Codex 配置失败") from exc
            finally:
                for temporary in (overlay, replacement):
                    try:
                        self._remove_exact(temporary)
                    except OSError:
                        # 发布结果已经由 home/backup 的目录状态确定；临时目录清理
                        # 失败不能掩盖原异常或把已提交的新家误报成未知状态。
                        pass
            return CodexInheritanceResult(
                inherited=True,
                copied_entries=tuple(copied_entries),
                agents_skill_count=agents_skill_count,
            )

    def _ensure_root(self) -> None:
        """创建并收紧托管根权限，同时拒绝根目录符号链接。"""

        if self.root.is_symlink():
            raise CodexConnectionHomeError("Codex 连接家根目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    @staticmethod
    def _copy_directory(source: Path, target: Path, *, label: str) -> None:
        """按当前链接目标复制目录，使发布副本不再依赖全局配置。

        Args:
            source: 本次继承读取的全局目录。
            target: staging 中不会覆盖现有连接内容的目标目录。
            label: 出错时可公开的配置入口名称，不包含完整本机路径。
        """

        if not source.is_dir():
            raise CodexConnectionHomeError(f"全局 Codex 配置目录不可继承：{label}")
        shutil.copytree(source, target, symlinks=False)

    @staticmethod
    def _copy_regular_file(source: Path, target: Path, *, label: str) -> None:
        """复制普通文件并移除 group 和 other 权限。

        Args:
            source: 本次继承读取的全局文件。
            target: staging 中的目标文件。
            label: 出错时可公开的配置入口名称。
        """

        if not source.is_file():
            raise CodexConnectionHomeError(f"全局 Codex 配置文件不可继承：{label}")
        shutil.copy2(source, target)
        target.chmod(target.stat().st_mode & 0o700)

    @staticmethod
    def _assert_forbidden_values_absent(
        stage: Path,
        forbidden_values: Iterable[str],
    ) -> None:
        """扫描稳定 staging，阻止 Trowel 已知凭据进入连接家。

        Args:
            stage: 即将覆盖发布的配置副本根。
            forbidden_values: 不允许出现在任何普通文件中的凭据原值。

        Raises:
            CodexConnectionHomeError: 副本仍含符号链接、无法读取或命中凭据。
        """

        canaries = tuple(
            value.encode("utf-8")
            for value in forbidden_values
            if isinstance(value, str) and value
        )
        for path in stage.rglob("*"):
            if path.is_symlink():
                raise CodexConnectionHomeError(
                    "全局 Codex 配置包含无法安全继承的符号链接"
                )
            if not path.is_file() or not canaries:
                continue
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise CodexConnectionHomeError(
                    "全局 Codex 配置无法完成凭据扫描"
                ) from exc
            if any(canary in content for canary in canaries):
                raise CodexConnectionHomeError(
                    "全局 Codex 配置中仍包含 Trowel 已知凭据，已拒绝继承"
                )

    @staticmethod
    def _write_private_json(path: Path, value: dict[str, object]) -> None:
        """以 ``0600`` 创建继承成功标记。"""

        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(value, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @classmethod
    def _publish(
        cls,
        home: Path,
        overlay: Path,
        replacement: Path,
        backup: Path,
    ) -> None:
        """先组装完整新连接家，再以可恢复的目录交换发布。

        调用方必须先让这项连接的 Codex manager 进入维护态；否则独立进程可能在
        两次目录 rename 的短窗口内看到路径暂时不存在。新目录先完整保留认证与
        非受管状态，再只替换约定入口；崩溃发生在交换中间时，下一次 ensure_home
        会从同事务 backup 恢复旧家。恢复不完整时禁止清理 backup。
        """

        shutil.copytree(home, replacement, symlinks=True)
        replacement.chmod(0o700)
        for relative in _MANAGED_PATHS:
            cls._ensure_relative_parent(replacement, relative.parent)
            cls._remove_exact(replacement / relative)
            staged = overlay / relative
            if staged.exists() or staged.is_symlink():
                cls._ensure_relative_parent(replacement, relative.parent)
                staged.rename(replacement / relative)

        home.rename(backup)
        try:
            replacement.rename(home)
        except BaseException as publish_error:
            try:
                backup.rename(home)
            except BaseException as rollback_error:
                raise CodexConnectionHomeError(
                    "Codex 配置发布失败且未能完整恢复；旧配置已保留在私有恢复副本中"
                ) from BaseExceptionGroup(
                    "Codex 配置发布与恢复同时失败",
                    [publish_error, rollback_error],
                )
            raise
        try:
            cls._remove_exact(backup)
        except OSError:
            # 新配置已完整发布，遗留旧副本比向调用方误报失败更安全。
            pass

    def _recover_interrupted_publish(self, connection_id: str, home: Path) -> None:
        """恢复进程中断留下的目录交换，并清理未开始交换的 staging。

        Args:
            connection_id: 用于精确匹配本连接事务目录的规范 UUID。
            home: 该连接稳定配置家路径。

        Raises:
            CodexConnectionHomeError: 恢复副本不安全或无法回到完整旧家。
        """

        backup_prefix = f".inherit-backup-{connection_id}-"
        stage_prefix = f".inherit-stage-{connection_id}-"
        overlay_prefix = f".inherit-overlay-{connection_id}-"
        try:
            for backup in sorted(self.root.glob(f"{backup_prefix}*"), key=str):
                if backup.is_symlink() or not backup.is_dir():
                    raise CodexConnectionHomeError(
                        "Codex 配置恢复副本路径不安全"
                    )
                transaction_id = backup.name.removeprefix(backup_prefix)
                replacement = self.root / f"{stage_prefix}{transaction_id}"
                if home.exists():
                    self._remove_exact(backup)
                else:
                    backup.rename(home)
                self._remove_exact(replacement)
            if not home.exists():
                return
            for pattern in (f"{stage_prefix}*", f"{overlay_prefix}*"):
                for orphan in self.root.glob(pattern):
                    self._remove_exact(orphan)
        except CodexConnectionHomeError:
            raise
        except OSError as exc:
            raise CodexConnectionHomeError(
                "Codex 配置中断事务无法安全恢复"
            ) from exc

    @staticmethod
    def _ensure_relative_parent(root: Path, relative_parent: Path) -> None:
        """在受控根内创建嵌套父目录，并拒绝沿途符号链接。

        Args:
            root: 已验证的连接家或本次私有 backup 根。
            relative_parent: 仅来自模块常量的相对父路径。

        Raises:
            CodexConnectionHomeError: 父路径越界、为符号链接或不是目录。
        """

        if relative_parent.is_absolute() or ".." in relative_parent.parts:
            raise CodexConnectionHomeError("Codex 配置发布路径越界")
        current = root
        for part in relative_parent.parts:
            if part in {"", "."}:
                continue
            current = current / part
            if current.is_symlink():
                raise CodexConnectionHomeError("Codex 配置发布父路径不能是符号链接")
            if current.exists():
                if not current.is_dir():
                    raise CodexConnectionHomeError("Codex 配置发布父路径不是目录")
                continue
            current.mkdir(mode=0o700)

    @staticmethod
    def _remove_exact(path: Path) -> None:
        """删除一个已解析的 staging 或 backup 入口而不跟随符号链接。"""

        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)
