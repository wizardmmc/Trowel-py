"""验证 moon 固定版本安装器的供应链与缓存边界。"""

from __future__ import annotations

import hashlib
import io
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from scripts.quality.installer import MoonInstallError, MoonInstaller, MoonRelease


def _archive_bytes() -> bytes:
    """生成只含可执行 moon 文件的本地 tar.xz 安装测试包。"""
    payload = b"#!/bin/sh\necho moon 2.4.6\n"
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:xz") as archive:
        info = tarfile.TarInfo("moon_cli-test/moon")
        info.mode = 0o755
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def test_installer_verifies_checksum_and_reuses_cached_binary(tmp_path: Path) -> None:
    """校验通过后原子安装，后续调用不再下载。"""
    archive = _archive_bytes()
    release = MoonRelease(
        version="2.4.6",
        target="test",
        sha256=hashlib.sha256(archive).hexdigest(),
    )
    downloads: list[str] = []

    def download(url: str) -> bytes:
        """记录下载地址并返回固定测试包。"""
        downloads.append(url)
        return archive

    installer = MoonInstaller(cache_root=tmp_path, release=release, download=download)

    first = installer.ensure()
    second = installer.ensure()

    assert first == second
    assert first.read_bytes().startswith(b"#!/bin/sh")
    assert first.stat().st_mode & 0o111
    assert len(downloads) == 1


def test_installer_rejects_checksum_mismatch_without_publishing_binary(
    tmp_path: Path,
) -> None:
    """下载内容不匹配官方哈希时不得留下可执行文件。"""
    release = MoonRelease(version="2.4.6", target="test", sha256="0" * 64)
    installer = MoonInstaller(
        cache_root=tmp_path, release=release, download=lambda _url: _archive_bytes()
    )

    with pytest.raises(MoonInstallError, match="SHA-256"):
        installer.ensure()

    assert not list(tmp_path.rglob("moon"))


def test_installer_publishes_one_valid_cache_under_concurrency(tmp_path: Path) -> None:
    """多个首次调用同时完成下载时不得争用固定临时文件。"""
    archive = _archive_bytes()
    release = MoonRelease(
        version="2.4.6",
        target="test",
        sha256=hashlib.sha256(archive).hexdigest(),
    )
    callers = 8
    downloads_ready = Barrier(callers)

    def download(_url: str) -> bytes:
        """让全部调用同时进入原子发布阶段。

        Args:
            _url: 安装器生成的固定测试 release 地址，本场景不访问网络。

        Returns:
            所有线程共用、哈希正确的测试压缩包。
        """
        downloads_ready.wait(timeout=5)
        return archive

    def install(_index: int) -> Path:
        """执行一次并发安装调用。

        Args:
            _index: 仅用于为线程池提供相同数量的调用，不参与安装行为。

        Returns:
            本次调用取得的共享 moon 缓存路径。
        """
        return installer.ensure()

    installer = MoonInstaller(cache_root=tmp_path, release=release, download=download)
    with ThreadPoolExecutor(max_workers=callers) as executor:
        installed = tuple(executor.map(install, range(callers)))

    assert len(set(installed)) == 1
    assert installed[0].read_bytes().startswith(b"#!/bin/sh")
    assert installed[0].stat().st_mode & 0o111
    assert installed[0].with_name("archive.sha256").read_text(
        encoding="ascii"
    ).strip() == (release.sha256)
