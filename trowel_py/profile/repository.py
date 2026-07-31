"""读取、校验、快照和写入文件型用户画像。"""

from __future__ import annotations

import re
from pathlib import Path

from trowel_py.memory.store.codec import (
    _coerce_meta_str,
    _dump_frontmatter,
    _split_frontmatter,
)
from trowel_py.profile.document import (
    body_to_profile,
    empty_profile,
    profile_to_body,
    validate_profile,
)
from trowel_py.profile.models import Profile

_PROFILE_FILE = "profile.md"


def _safe_snapshot_name(value: object) -> str:
    """把画像元数据整理为安全的快照文件名片段。

    斜杠、反斜杠和 NUL 字节序列替换为下划线，再去掉两端空白与开头句点；
    结果为空时返回 ``unknown``。
    """
    text = re.sub(r"[\\/\x00]+", "_", _coerce_meta_str(value)).strip().lstrip(".")
    return text or "unknown"


class ProfileRepository:
    """管理一个 Memory 根目录中的画像正文和历史快照。

    写入前会备份已有文件，但正式文件和快照都通过 ``write_text()`` 直接写入，
    不提供原子替换或并发写保护；I/O、UTF-8 和 YAML 序列化错误直接传播。

    Attributes:
        root: ``profile.md`` 和 ``meta/profile-history`` 所在的 Memory 根目录。
    """

    def __init__(self, root: Path | str) -> None:
        """保存画像文件树的根路径。

        Args:
            root: Memory 文件树的根路径，可为相对路径。
        """
        self.root = Path(root)

    def load_profile(self) -> Profile:
        """读取 ``profile.md``，并宽松解析五个 Profile 维度。

        文件缺失，或 frontmatter 缺失、为空、YAML 无法解析或不是映射时返回
        空 Profile。其他映射不校验 schema：``updated`` 和 ``source`` 均宽松
        转为文本，其中 YAML 日期使用 ISO 格式；假值 ``source`` 回退为
        ``user-edit``，其他来源不校验允许值。
        """
        path = self.root / _PROFILE_FILE
        if not path.exists():
            return empty_profile()
        fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if not fm:
            return empty_profile()
        return body_to_profile(
            body,
            updated=_coerce_meta_str(fm.get("updated")),
            source=_coerce_meta_str(fm.get("source")) or "user-edit",
        )

    def write_profile(self, profile: Profile, *, source: str) -> None:
        """校验 Profile，快照已有文件，再覆盖 ``profile.md``。

        ``source`` 是本次写入的权威来源，``profile.source`` 不落盘。已有文件
        无论内容是否有效都会先快照；快照失败时不写正式文件。正式文件只保存
        ``updated``、``source`` 和五维正文，写入不是原子替换。

        Args:
            profile: 提供五个正文维度和更新时间的 Profile。
            source: 写入 frontmatter 的来源。

        Raises:
            ValueError: Profile 维度、更新时间或来源未通过校验。
        """
        validate_profile(profile, source)
        path = self.root / _PROFILE_FILE
        if path.exists():
            self._snapshot_profile(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {"updated": profile.updated, "source": source}
        path.write_text(
            _dump_frontmatter(frontmatter, profile_to_body(profile)),
            encoding="utf-8",
        )

    def _snapshot_profile(self, path: Path) -> None:
        """把指定 Profile 文本写入历史目录的未占用文件。

        文件名使用 frontmatter 的 ``updated`` 和 ``source``；frontmatter 无效
        或字段为空时对应片段为 ``unknown``。冲突时从 ``-2`` 开始递增。名称
        选择与写入之间没有锁，并发调用不能保证各自获得不同路径。

        Args:
            path: 要读取并快照的现有文本文件。
        """
        text = path.read_text(encoding="utf-8")
        fm, _body = _split_frontmatter(text)
        updated = _safe_snapshot_name((fm or {}).get("updated"))
        source = _safe_snapshot_name((fm or {}).get("source"))
        history_dir = self.root / "meta" / "profile-history"
        history_dir.mkdir(parents=True, exist_ok=True)
        target = history_dir / f"{updated}-{source}.md"
        suffix = 2
        while target.exists():
            target = history_dir / f"{updated}-{source}-{suffix}.md"
            suffix += 1
        target.write_text(text, encoding="utf-8")
