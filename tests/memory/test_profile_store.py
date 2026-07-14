"""slice-047 profile data model + profile.md storage tests (TDD RED → GREEN).

Covers the C-1..C-7 invariants from docs/slices/activate/slice-047.md:
five-dim round-trip, per-dim independence, empty/missing-file degradation
(C-6), updated/source stamping, snapshot insurance on overwrite + no-snapshot
on first write (C-4), validate_profile rejection of bad source / missing
updated / non-str dims. All via tmp_path fixtures — no real memory DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.memory.profile import (
    body_to_profile,
    empty_profile,
    profile_to_body,
    validate_profile,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.memory.types import Profile


# ---------- profile.py unit helpers ----------


def test_body_roundtrip_five_dims() -> None:
    p = Profile(
        ability="网安硕士 / 红队",
        methodology="spec-first，spike 实测",
        expression="大白话，禁翻译腔",
        goal="反诈论文 + trowel",
        other="在啃保形预测",
        updated="2026-07-14",
    )
    body = profile_to_body(p)
    assert "## 能力水平" in body
    assert "## 方法论偏好" in body
    assert "## 表达风格" in body
    assert "## 长程目标" in body
    assert "## 其他" in body
    back = body_to_profile(body, updated="2026-07-14", source="user-edit")
    assert back == Profile(
        ability="网安硕士 / 红队",
        methodology="spec-first，spike 实测",
        expression="大白话，禁翻译腔",
        goal="反诈论文 + trowel",
        other="在啃保形预测",
        updated="2026-07-14",
        source="user-edit",
    )


def test_body_multiline_roundtrips() -> None:
    # free-text body with blank lines must survive the section split intact
    body = "line1\nline2\n\nline3"
    p = Profile(ability=body, updated="2026-07-14")
    assert body_to_profile(profile_to_body(p), updated="2026-07-14", source="user-edit").ability == body


def test_body_missing_section_becomes_empty() -> None:
    # only one section present in a hand-written body — others degrade to ""
    body = "## 能力水平\njust ability\n"
    p = body_to_profile(body, updated="2026-07-14", source="user-edit")
    assert p.ability == "just ability"
    assert p.methodology == ""
    assert p.other == ""


# ---------- MemoryStore.load_profile / write_profile ----------


def test_roundtrip_five_dims(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    p = Profile(
        ability="网安硕士",
        methodology="spec-first",
        expression="大白话",
        goal="反诈论文",
        other="保形预测",
        updated="2026-07-14",
    )
    store.write_profile(p, source="user-edit")
    loaded = store.load_profile()
    assert loaded.ability == "网安硕士"
    assert loaded.methodology == "spec-first"
    assert loaded.expression == "大白话"
    assert loaded.goal == "反诈论文"
    assert loaded.other == "保形预测"
    assert loaded.updated == "2026-07-14"
    assert loaded.source == "user-edit"


def test_each_dim_independent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_profile(Profile(ability="ONLY_ABILITY", updated="2026-07-14"), source="user-edit")
    loaded = store.load_profile()
    assert loaded.ability == "ONLY_ABILITY"
    assert loaded.methodology == ""
    assert loaded.expression == ""
    assert loaded.goal == ""
    assert loaded.other == ""


def test_multiline_dim_roundtrips_through_store(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_profile(
        Profile(methodology="step1\nstep2\n\nstep3", updated="2026-07-14"),
        source="user-edit",
    )
    assert store.load_profile().methodology == "step1\nstep2\n\nstep3"


def test_missing_file_returns_empty_profile(tmp_path: Path) -> None:
    # C-6: no profile.md → empty Profile, no raise
    assert MemoryStore(tmp_path).load_profile() == empty_profile()


def test_empty_file_returns_empty_profile(tmp_path: Path) -> None:
    (tmp_path / "profile.md").write_text("", encoding="utf-8")
    assert MemoryStore(tmp_path).load_profile() == empty_profile()


def test_frontmatter_only_no_body_returns_empty_dims(tmp_path: Path) -> None:
    # lenient: frontmatter present, no ## sections → empty dims, meta kept
    (tmp_path / "profile.md").write_text(
        "---\nupdated: '2026-07-14'\nsource: user-edit\n---\n",
        encoding="utf-8",
    )
    loaded = MemoryStore(tmp_path).load_profile()
    assert loaded.updated == "2026-07-14"
    assert loaded.source == "user-edit"
    assert loaded.ability == ""
    assert loaded.goal == ""


def test_source_param_is_authoritative_stamp(tmp_path: Path) -> None:
    # source arg overrides whatever p.source carried (immutability routing)
    store = MemoryStore(tmp_path)
    p = Profile(ability="A", updated="2026-07-14", source="user-edit")
    store.write_profile(p, source="ai-calibration")
    assert store.load_profile().source == "ai-calibration"


# ---------- C-4 snapshot insurance ----------


def test_snapshot_on_overwrite(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_profile(
        Profile(ability="V1", updated="2026-07-13", source="user-edit"),
        source="user-edit",
    )
    store.write_profile(
        Profile(ability="V2", updated="2026-07-14", source="user-edit"),
        source="user-edit",
    )
    hist = tmp_path / "meta" / "profile-history"
    assert hist.exists()
    snaps = sorted(hist.glob("*.md"))
    assert len(snaps) == 1
    assert "V1" in snaps[0].read_text(encoding="utf-8")


def test_first_write_creates_no_snapshot(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_profile(Profile(ability="V1", updated="2026-07-14"), source="user-edit")
    hist = tmp_path / "meta" / "profile-history"
    assert not hist.exists() or list(hist.glob("*.md")) == []


def test_snapshot_disambiguates_same_key(tmp_path: Path) -> None:
    # two overwrites where the OLD profile keeps the same (updated, source):
    # the second snapshot must not clobber the first (insurance, not overwrite)
    store = MemoryStore(tmp_path)
    for content in ("V1", "V2", "V3"):
        store.write_profile(
            Profile(ability=content, updated="2026-07-14", source="user-edit"),
            source="user-edit",
        )
    snaps = sorted((tmp_path / "meta" / "profile-history").glob("*.md"))
    assert len(snaps) == 2  # V1 + V2 snapshotted; V3 is the live file
    blob = " ".join(s.read_text(encoding="utf-8") for s in snaps)
    assert "V1" in blob
    assert "V2" in blob


# ---------- validate_profile ----------


def test_validate_rejects_bad_source(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MemoryStore(tmp_path).write_profile(
            Profile(ability="A", updated="2026-07-14"), source="bogus"
        )


def test_validate_rejects_missing_updated(tmp_path: Path) -> None:
    # C-3: updated is required (ISO date) on every write
    with pytest.raises(ValueError):
        MemoryStore(tmp_path).write_profile(Profile(ability="A"), source="user-edit")


def test_validate_rejects_nonstr_dim() -> None:
    # frozen dataclass has no runtime type check; validate_profile must catch it
    bad = Profile(ability=123, updated="2026-07-14")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        validate_profile(bad, "user-edit")


# ---------- CR fixes (W1/W2/W3): hand-edited profile.md edge cases ----------


def test_body_unknown_subheading_kept_as_text() -> None:
    # W1: a `## sub` inside a known section is NOT a section boundary — its line
    # and content stay in the enclosing dimension (no silent content loss).
    body = "## 能力水平\nintro\n## sub\ndeep\n"
    p = body_to_profile(body, updated="2026-07-14", source="user-edit")
    assert p.ability == "intro\n## sub\ndeep"


def test_snapshot_name_sanitized_against_traversal(tmp_path: Path) -> None:
    # W2: a hand-edited `updated` with path separators must not escape the
    # history dir (no ../../ traversal, no stray file in the memory root).
    (tmp_path / "profile.md").write_text(
        "---\nupdated: ../../etc\nsource: user-edit\n---\n## 能力水平\nold\n",
        encoding="utf-8",
    )
    MemoryStore(tmp_path).write_profile(
        Profile(ability="new", updated="2026-07-14"), source="user-edit"
    )
    hist = tmp_path / "meta" / "profile-history"
    snaps = list(hist.glob("*.md"))
    assert len(snaps) == 1
    assert snaps[0].parent.resolve() == hist.resolve()
    assert not (tmp_path / "etc-user-edit.md").exists()


def test_load_normalizes_unquoted_yaml_date(tmp_path: Path) -> None:
    # W3: unquoted `updated: 2026-07-14` parses to datetime.date; load must
    # normalize to an ISO string, not a "datetime.date(...)" repr or space form.
    (tmp_path / "profile.md").write_text(
        "---\nupdated: 2026-07-14\nsource: user-edit\n---\n## 能力水平\nx\n",
        encoding="utf-8",
    )
    assert MemoryStore(tmp_path).load_profile().updated == "2026-07-14"
