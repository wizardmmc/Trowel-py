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
    root: Path

    def load_profile(self) -> Profile:

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
        """校验后先快照旧 profile，再覆盖正式文件。"""

        validate_profile(p, source)
        path = self.root / _PROFILE_FILE
        if path.exists():
            self._snapshot_profile(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fm = {"updated": p.updated, "source": source}
        path.write_text(_dump_frontmatter(fm, profile_to_body(p)), encoding="utf-8")

    def _snapshot_profile(self, path: Path) -> None:

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
