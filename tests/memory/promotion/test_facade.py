from pathlib import Path

from trowel_py.memory import promotion
from trowel_py.memory.promotion_policy import PromotionPolicy
from trowel_py.memory.recompute import NoteEffect
from trowel_py.memory.types import Note


def _eligible_evidence() -> tuple[Note, NoteEffect]:
    return (
        Note(
            type="note",
            title="a",
            kind="gotcha",
            verification="verified",
            memory_id="a",
            body="body",
        ),
        NoteEffect(
            stem="a",
            memory_id="a",
            refs=1,
            read_sessions=frozenset({"cc-1"}),
            helpful_sessions=frozenset({"cc-1"}),
            harmful_sessions=frozenset(),
            unused_sessions=frozenset(),
            read_dates=frozenset({"2026-07-01"}),
            helpful_read_dates=frozenset({"2026-07-01"}),
        ),
    )


def test_evaluate_promotion_keeps_facade_identity_and_write_seam(
    tmp_path: Path, monkeypatch
) -> None:
    note, effect = _eligible_evidence()
    writes: list[str] = []

    class FakeStore:
        def __init__(self, root: Path) -> None:
            assert root == tmp_path

        def load_notes_with_id(self):
            return [("a", note)]

    def fake_write_candidate(root, candidate, _effect, _policy, today):
        assert root == tmp_path
        assert candidate is note
        assert _effect is effect
        assert today == "2026-07-24"
        writes.append(candidate.memory_id)
        return tmp_path / "meta" / "core-candidates" / "a.md"

    monkeypatch.setattr(
        promotion,
        "compute_note_effects",
        lambda root, local_tz=None: {"a": effect},
    )
    monkeypatch.setattr(promotion, "MemoryStore", FakeStore)
    monkeypatch.setattr(promotion, "_write_candidate", fake_write_candidate)

    report = promotion.evaluate_promotion(
        tmp_path,
        PromotionPolicy(min_helpful_sessions=1, min_distinct_days=1),
        today="2026-07-24",
    )

    assert promotion.evaluate_promotion.__module__ == "trowel_py.memory.promotion"
    assert report["candidates"] == ["a"]
    assert writes == ["a"]


def test_candidate_helpers_resolve_chained_dependencies_on_facade(
    tmp_path: Path, monkeypatch
) -> None:
    note, effect = _eligible_evidence()
    policy = PromotionPolicy(min_helpful_sessions=1, min_distinct_days=1)
    candidate = tmp_path / "patched" / "candidate.md"
    split_frontmatter = promotion._split_frontmatter

    monkeypatch.setattr(
        promotion, "_safe_candidate_path", lambda root, memory_id: candidate
    )
    monkeypatch.setattr(
        promotion, "_candidate_body", lambda note, effect, policy: "patched body"
    )
    monkeypatch.setattr(promotion, "_policy_hash", lambda policy: "patched-policy")
    monkeypatch.setattr(promotion, "_hash_ids", lambda ids: "patched-sessions")

    written = promotion._write_candidate(tmp_path, note, effect, policy, "2026-07-24")

    assert written == candidate
    frontmatter, body = split_frontmatter(candidate.read_text(encoding="utf-8"))
    assert frontmatter["policy_hash"] == "patched-policy"
    assert frontmatter["helpful_session_ids_hash"] == "patched-sessions"
    assert body == "patched body"

    candidate.write_text("broken", encoding="utf-8")
    monkeypatch.setattr(
        promotion,
        "_split_frontmatter",
        lambda raw: ({"status": "candidate"}, "preserved body"),
    )
    promotion._mark_blocked(candidate, policy, "2026-07-25", "harmful_sessions=1>0")

    frontmatter, body = split_frontmatter(candidate.read_text(encoding="utf-8"))
    assert frontmatter["status"] == "blocked"
    assert frontmatter["blocked_reason"] == "harmful_sessions=1>0"
    assert body == "preserved body"
