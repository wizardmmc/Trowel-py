"""Profile 的读取、校验、快照与写入。"""

from __future__ import annotations

from pathlib import Path

from trowel_py.memory.profile import (
    body_to_profile,
    empty_profile,
    profile_to_body,
    validate_profile,
)
from trowel_py.memory.types import Profile

from .codec import (
    _coerce_meta_str,
    _dump_frontmatter,
    _safe_snapshot_name,
    _split_frontmatter,
)

_PROFILE_FILE = "profile.md"


class _ProfileStore:
    """为 ``MemoryStore`` 提供 Profile 读取、快照和写入能力。

    组合后的仓储必须提供 ``root``。写入前会备份已有文件，但正式文件和快照
    都通过 ``write_text()`` 直接写入，不提供原子替换或并发写保护；I/O、
    UTF-8 和 YAML 序列化错误直接传播。
    """

    root: Path

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

    def write_profile(self, p: Profile, *, source: str) -> None:
        """校验 Profile，快照已有文件，再覆盖 ``profile.md``。

        ``source`` 是本次写入的权威来源，``p.source`` 不落盘。已有文件无论
        内容是否有效都会先快照；快照失败时不写正式文件。正式文件只保存
        ``updated``、``source`` 和五维正文，写入不是原子替换。

        Args:
            p: 提供五个正文维度和更新时间的 Profile。
            source: 写入 frontmatter 的来源。

        Raises:
            ValueError: Profile 维度、更新时间或来源未通过校验。
        """

        validate_profile(p, source)
        path = self.root / _PROFILE_FILE
        if path.exists():
            self._snapshot_profile(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = {"updated": p.updated, "source": source}
        path.write_text(_dump_frontmatter(fm, profile_to_body(p)), encoding="utf-8")

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
        hist_dir = self.root / "meta" / "profile-history"
        hist_dir.mkdir(parents=True, exist_ok=True)
        target = hist_dir / f"{updated}-{source}.md"
        n = 2
        while target.exists():
            target = hist_dir / f"{updated}-{source}-{n}.md"
            n += 1
        target.write_text(text, encoding="utf-8")
