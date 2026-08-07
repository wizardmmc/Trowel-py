"""验证研讨正文路径和内容完整性边界。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trowel_py.discussion.artifacts import DiscussionArtifactStore


def test_verified_path_rejects_absolute_and_parent_escape(tmp_path: Path) -> None:
    """数据库引用不能使用绝对路径或逃出 discussion 私有目录。"""

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")

    with pytest.raises(ValueError, match="must be relative"):
        artifacts.verified_path(
            str(outside.resolve()),
            expected_sha256=hashlib.sha256(b"outside").hexdigest(),
        )
    with pytest.raises(ValueError, match="escapes discussion root"):
        artifacts.verified_path(
            "discussions/../outside.md",
            expected_sha256=hashlib.sha256(b"outside").hexdigest(),
        )


def test_verified_path_rejects_symlink_escape(tmp_path: Path) -> None:
    """discussion 目录内的符号链接也不能指向数据根外。"""

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    link = artifacts.data_root / "discussions" / "linked.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    with pytest.raises(ValueError, match="escapes discussion root"):
        artifacts.verified_path(
            "discussions/linked.md",
            expected_sha256=hashlib.sha256(b"outside").hexdigest(),
        )


def test_verified_path_rejects_byte_count_drift(tmp_path: Path) -> None:
    """正文实际字节数与 SQLite 记录不符时不能返回路径。"""

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    output = artifacts.data_root / "discussions" / "one" / "final.md"
    output.parent.mkdir(parents=True)
    body = "完整回答".encode()
    output.write_bytes(body)

    with pytest.raises(ValueError, match="byte count mismatch"):
        artifacts.verified_path(
            "discussions/one/final.md",
            expected_sha256=hashlib.sha256(body).hexdigest(),
            expected_bytes=len(body) + 1,
        )
