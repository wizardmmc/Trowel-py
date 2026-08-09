"""下载、校验并缓存质量任务固定使用的 moon 二进制。"""

from __future__ import annotations

import hashlib
import os
import platform
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

MOON_VERSION = "2.4.6"
RELEASE_SHA256 = {
    "aarch64-apple-darwin": "5bc863dd2c5e18c11e35a035318e6a3b5aaa7636f6c16ec6bd6366baf031e596",
    "x86_64-unknown-linux-gnu": "106a4b18ddd93e9485a396c14b3a7e287586a006ea201e6a0b37e9e221f51d97",
    "aarch64-unknown-linux-gnu": "5eb8afeae1afca5a74efe8db9aafb1fc47ca5c10a0fe976b740f3ad45b3a5cae",
}


def _atomic_publish(path: Path, payload: bytes, *, mode: int) -> None:
    """用独占临时文件原子发布一份缓存内容。

    Args:
        path: 多个质量进程可能同时写入的最终缓存路径。
        payload: 需要完整写入并一次替换到最终路径的字节。
        mode: 最终文件的 POSIX 权限位。
    """
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
            temporary.write(payload)
            temporary_path = Path(temporary.name)
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class MoonInstallError(RuntimeError):
    """表示 moon 平台不支持、下载损坏或发布包缺少可执行文件。"""


@dataclass(frozen=True)
class MoonRelease:
    """描述一个经过固定哈希校验的 moon 官方发布包。

    Attributes:
        version: 仓库固定使用的 moon 正式版本。
        target: moon 官方发布资产中的平台三元组。
        sha256: 官方发布页提供的压缩包 SHA-256。
    """

    version: str
    target: str
    sha256: str

    @property
    def archive_name(self) -> str:
        """返回 GitHub release 中的压缩包文件名。"""
        return f"moon_cli-{self.target}.tar.xz"

    @property
    def url(self) -> str:
        """返回固定版本官方压缩包地址。"""
        return f"https://github.com/moonrepo/moon/releases/download/v{self.version}/{self.archive_name}"


def release_for_current_platform() -> MoonRelease:
    """把当前系统与处理器映射到已审核的 moon 发布包。"""
    system = platform.system().lower()
    machine = platform.machine().lower()
    targets = {
        ("darwin", "arm64"): "aarch64-apple-darwin",
        ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
        ("linux", "amd64"): "x86_64-unknown-linux-gnu",
        ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
        ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    }
    try:
        target = targets[(system, machine)]
    except KeyError as error:
        raise MoonInstallError(
            f"moon 2.4.6 is not pinned for {system}/{machine}"
        ) from error
    return MoonRelease(MOON_VERSION, target, RELEASE_SHA256[target])


def _download(url: str) -> bytes:
    """从官方 release 下载 moon 压缩包字节。

    Args:
        url: 固定 moon 版本和平台的 GitHub Release 资产地址。

    Returns:
        尚未解压、等待 SHA-256 校验的压缩包字节。
    """
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def default_cache_root() -> Path:
    """返回可由环境覆盖的用户级质量工具缓存目录。"""
    configured = os.environ.get("TROWEL_QUALITY_CACHE")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".cache" / "trowel" / "quality"


class MoonInstaller:
    """在用户缓存中原子准备一个经过官方哈希校验的 moon。"""

    def __init__(
        self,
        *,
        cache_root: Path,
        release: MoonRelease,
        download: Callable[[str], bytes] = _download,
    ) -> None:
        """配置缓存位置、固定发布包和可替换下载边界。

        Args:
            cache_root: 不进入仓库的质量工具缓存根目录。
            release: 本次允许安装的 moon 版本、平台和官方哈希。
            download: 根据 URL 返回压缩包字节的下载实现。
        """
        self._cache_root = cache_root
        self._release = release
        self._download = download

    @property
    def _install_dir(self) -> Path:
        """返回当前版本与平台独占的安装目录。"""
        return self._cache_root / "moon" / self._release.version / self._release.target

    def _cached_binary(self) -> Path | None:
        """仅在版本哈希标记匹配时返回已有可执行文件。"""
        executable = self._install_dir / "moon"
        marker = self._install_dir / "archive.sha256"
        if not executable.is_file() or not marker.is_file():
            return None
        if marker.read_text(encoding="ascii").strip() != self._release.sha256:
            return None
        if not os.access(executable, os.X_OK):
            return None
        return executable

    def _extract_executable(self, archive_bytes: bytes) -> bytes:
        """只读取发布包中预期路径的 moon 文件，不展开其它成员。

        Args:
            archive_bytes: 已通过当前 release SHA-256 校验的 tar.xz 字节。

        Returns:
            发布包中固定路径的 moon 可执行文件字节。
        """
        member_name = f"moon_cli-{self._release.target}/moon"
        try:
            with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:xz") as archive:
                member = archive.getmember(member_name)
                source = archive.extractfile(member)
                if source is None or not member.isfile():
                    raise MoonInstallError(
                        f"moon release member is not a file: {member_name}"
                    )
                return source.read()
        except (KeyError, tarfile.TarError) as error:
            raise MoonInstallError(f"moon release is missing {member_name}") from error

    def _publish(self, executable_bytes: bytes) -> Path:
        """原子发布可执行文件和对应压缩包哈希标记。

        Args:
            executable_bytes: 从已校验官方压缩包读取的 moon 文件字节。

        Returns:
            当前版本与平台缓存中的 moon 可执行文件路径。
        """
        self._install_dir.mkdir(parents=True, exist_ok=True)
        executable = self._install_dir / "moon"
        _atomic_publish(executable, executable_bytes, mode=0o755)
        _atomic_publish(
            self._install_dir / "archive.sha256",
            f"{self._release.sha256}\n".encode("ascii"),
            mode=0o644,
        )
        return executable

    def ensure(self) -> Path:
        """返回缓存 moon；缺失时下载、校验并原子安装。"""
        cached = self._cached_binary()
        if cached is not None:
            return cached
        archive_bytes = self._download(self._release.url)
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if actual_sha256 != self._release.sha256:
            raise MoonInstallError(
                "moon archive SHA-256 mismatch: "
                f"expected {self._release.sha256}, got {actual_sha256}"
            )
        return self._publish(self._extract_executable(archive_bytes))


def ensure_moon() -> Path:
    """使用仓库固定版本和默认用户缓存准备当前平台的 moon。"""
    installer = MoonInstaller(
        cache_root=default_cache_root(),
        release=release_for_current_platform(),
    )
    return installer.ensure()
