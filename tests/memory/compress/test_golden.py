"""显式集成测试：仅在获准后读取本机 gitignored episode 副本。"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from trowel_py.memory.compress import compress_daily
from trowel_py.memory.store import MemoryStore, _split_frontmatter

_REAL_EPISODES = Path.home() / ".trowel" / "memory" / "episodes"
_TARGET_DATE = "2026-07-17"


def _integration_enabled() -> bool:
    return os.environ.get("CC_INTEGRATION") == "1"


def _covering_episodes() -> list[Path]:
    if not _REAL_EPISODES.is_dir():
        return []
    paths: list[Path] = []
    for path in sorted(_REAL_EPISODES.glob("*.md")):
        frontmatter, _body = _split_frontmatter(path.read_text(encoding="utf-8"))
        if not frontmatter:
            continue
        dates = frontmatter.get("activity_dates") or []
        if any(str(date) == _TARGET_DATE for date in dates):
            paths.append(path)
    return paths


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _integration_enabled() or not _covering_episodes(),
        reason="需要 CC_INTEGRATION=1 且本机存在目标日期 episode",
    ),
]


class ObedientProvider:
    """生成带真实来源别名的通用结构化响应。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        marker = "【segment "
        segment = "unknown-seg"
        index = user_prompt.find(marker)
        if index >= 0:
            segment = user_prompt[index + len(marker) :].split("】", 1)[0]
        items: list[tuple[str, str, str]] = [
            ("outcome", "完成当日示例任务并通过相关测试", segment),
            ("outcome", "核对派生摘要与来源记录", segment),
            ("decision", "保留结构化摘要和可追溯来源", segment),
            ("correction", "原摘要策略不准确，已改用派生缓存", segment),
            ("correction", "原日期归属不准确，已按活动日期投影", segment),
            ("open_loop", "后续验证 weekly 与 monthly 链路", segment),
            ("open_loop", "摘要措辞仍需人工复核", segment),
        ]
        for number in range(8):
            items.append(
                (
                    "outcome",
                    f"支撑细节项{number}：可由预算淘汰的完整结果描述",
                    segment,
                )
            )
        return json.dumps(
            {
                "items": [
                    {"type": item_type, "text": text, "source": source}
                    for item_type, text, source in items
                ]
            }
        )


def test_real_daily_under_budget_with_structure(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    (memory_root / "episodes").mkdir(parents=True)
    for source in _covering_episodes():
        shutil.copy2(source, memory_root / "episodes" / source.name)

    provider = ObedientProvider()
    assert (
        compress_daily(memory_root, _TARGET_DATE, provider)
        == _TARGET_DATE
    )

    [entry] = MemoryStore(memory_root).load_diary(layer="day")
    assert entry.date == _TARGET_DATE
    assert len(entry.body) <= 800
    assert entry.body.startswith(f"# {_TARGET_DATE}")
    assert "## 更正" in entry.body
    assert "## 待续" in entry.body
    for banned in ("表现不错", "认真检查", "全程高价值", "绩效", "反复确认"):
        assert banned not in entry.body
    assert "…" not in entry.body

    frontmatter, _body = _split_frontmatter(
        (
            memory_root / "diary" / "daily" / f"{_TARGET_DATE}.md"
        ).read_text(encoding="utf-8")
    )
    assert frontmatter["generation_status"] == "ok"
    assert frontmatter["source_hash"]
    assert len(frontmatter["source_segments"]) >= len(_covering_episodes())


def test_real_daily_idempotent_rerun_skips_llm(tmp_path: Path) -> None:
    memory_root = tmp_path / "memory"
    (memory_root / "episodes").mkdir(parents=True)
    for source in _covering_episodes():
        shutil.copy2(source, memory_root / "episodes" / source.name)

    provider = ObedientProvider()
    compress_daily(memory_root, _TARGET_DATE, provider)
    first_body = MemoryStore(memory_root).load_diary(layer="day")[0].body
    first_calls = len(provider.calls)

    provider2 = ObedientProvider()
    compress_daily(memory_root, _TARGET_DATE, provider2)
    assert provider2.calls == []
    assert len(provider.calls) == first_calls
    assert MemoryStore(memory_root).load_diary(layer="day")[0].body == first_body
