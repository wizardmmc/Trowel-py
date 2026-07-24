from __future__ import annotations

from tests.model_os.context_observer.support import (
    cc_asst,
    cc_boundary,
    force_window,
    _load_cc_fixture,
)
from trowel_py.model_os.context_observer import (
    ContextConfidence,
    UnavailableReason,
    extract_cc_samples,
    latest_main_ratio,
)


class TestCcDedup:
    def test_same_id_identical_usage_collapses_to_one(self):
        events = [
            cc_asst(message_id="msg_a", input_tokens=1000, output_tokens=50),
            cc_asst(message_id="msg_a", input_tokens=1000, output_tokens=50),
        ]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert len(samples) == 1
        assert samples[0].used_tokens == 1050

    def test_same_id_evolving_usage_keeps_last(self):
        events = [
            cc_asst(message_id="msg_x", input_tokens=0, output_tokens=0),
            cc_asst(message_id="msg_x", input_tokens=0, output_tokens=0),
            cc_asst(
                message_id="msg_x",
                input_tokens=7365,
                cache_read=49600,
                output_tokens=337,
            ),
        ]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="subagent",
            window_resolver=force_window(200_000),
        )
        assert len(samples) == 1
        assert samples[0].used_tokens == 7365 + 49600 + 337
        assert samples[0].input_tokens == 7365

    def test_distinct_ids_each_get_one_sample(self):
        events = [
            cc_asst(message_id="a", input_tokens=100),
            cc_asst(message_id="b", input_tokens=200),
        ]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert [s.request_identity for s in samples] == ["a", "b"]


class TestCcGeneration:
    def test_boundary_increments_generation(self):
        events = [
            cc_asst(message_id="a", input_tokens=180_000),
            cc_boundary(),
            cc_asst(message_id="b", input_tokens=60_000),
            cc_boundary(),
            cc_asst(message_id="c", input_tokens=50_000),
        ]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert [s.generation for s in samples] == [0, 1, 2]

    def test_ratio_uses_new_generation_window_not_old_peak(self):
        events = [
            cc_asst(message_id="pre", input_tokens=180_000, output_tokens=5000),
            cc_boundary(),
            cc_asst(message_id="post", input_tokens=60_000, output_tokens=2000),
        ]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        pre, post = samples
        assert pre.ratio > 0.9 and post.ratio < 0.4
        assert latest_main_ratio(samples) is post


class TestCcUnavailable:
    def test_real_model_missing_usage_dict_yields_unavailable(self):
        events = [cc_asst(message_id="m", usage={})]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert len(samples) == 1
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.MISSING_USAGE
        assert samples[0].ratio is None and samples[0].used_tokens is None

    def test_redacted_usage_block_yields_unavailable(self):
        events = [cc_asst(message_id="m", usage=None)]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert samples[0].unavailable_reason is UnavailableReason.REDACTED_USAGE

    def test_unknown_model_yields_unknown_window(self):
        events = [cc_asst(message_id="m", model="future-model-9", input_tokens=1000)]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
        )
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.UNKNOWN_WINDOW
        assert samples[0].used_tokens == 1000 and samples[0].ratio is None

    def test_synthetic_assistant_skipped_entirely(self):
        events = [
            cc_asst(message_id="synth", model="<synthetic>", input_tokens=10),
            cc_asst(message_id="real", input_tokens=500),
        ]
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert [s.request_identity for s in samples] == ["real"]


class TestCcWindowDynamic:
    def test_window_re_resolved_per_sample_when_model_changes(self):
        events = [
            cc_asst(message_id="a", model="glm-5.2", input_tokens=50_000),
            cc_asst(
                message_id="b",
                model="claude-sonnet-4",
                input_tokens=50_000,
            ),
        ]

        def resolver(model):
            return 200_000 if model == "glm-5.2" else 400_000

        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=resolver,
        )
        assert samples[0].effective_window_tokens == 200_000
        assert samples[1].effective_window_tokens == 400_000
        assert samples[0].ratio != samples[1].ratio


class TestCcSubagentIsolation:
    def test_subagent_samples_tagged_and_excluded_from_main(self):
        main = extract_cc_samples(
            [cc_asst(message_id="main_a", input_tokens=40_000)],
            native_session_id="s",
            main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        sub = extract_cc_samples(
            [cc_asst(message_id="sub_a", input_tokens=300_000)],
            native_session_id="s",
            main_or_subagent="subagent",
            window_resolver=force_window(200_000),
        )
        assert all(s.main_or_subagent == "main" for s in main)
        assert all(s.main_or_subagent == "subagent" for s in sub)
        combined = main + sub
        latest = latest_main_ratio(combined)
        assert latest is not None
        assert latest.request_identity == "main_a"


class TestCcFixtureMultiTurn:
    def test_unique_request_count_and_first_sample(self):
        events = _load_cc_fixture("glm-5.2-multi-turn.jsonl")
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
            source_version="2.1.197",
        )
        assert len(samples) == 11
        assert samples[0].used_tokens == 52929 + 0 + 0 + 225
        assert samples[0].ratio == round(53154 / 200_000, 4)
        assert samples[0].source_version == "2.1.197"

    def test_cache_read_monotonic_within_generation(self):
        events = _load_cc_fixture("glm-5.2-multi-turn.jsonl")
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
        )
        gen0 = [s for s in samples if s.generation == 0]
        reads = [s.cache_read_input_tokens for s in gen0]
        assert reads == sorted(reads)

    def test_window_is_200k_for_glm52(self):
        events = _load_cc_fixture("glm-5.2-multi-turn.jsonl")
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
        )
        assert all(s.effective_window_tokens == 200_000 for s in samples)


class TestCcFixtureCompactBoundary:
    def test_two_generations_and_peak_above_window(self):
        events = _load_cc_fixture("glm-5.2-compact-boundary.jsonl")
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="main",
        )
        gens = {s.generation for s in samples}
        assert gens == {0, 1}
        gen0 = [s for s in samples if s.generation == 0]
        gen1 = [s for s in samples if s.generation == 1]
        assert gen0[-1].used_tokens == 215920
        assert gen0[-1].ratio > 1.0
        assert gen1[0].used_tokens < gen0[-1].used_tokens

    def test_compact_trigger_is_manual_not_auto(self):
        events = _load_cc_fixture("glm-5.2-compact-boundary.jsonl")
        boundaries = [e for e in events if e.subtype == "compact_boundary"]
        assert boundaries, "fixture should carry at least one compact_boundary"
        assert all(e.compact_trigger == "manual" for e in boundaries)


class TestCcFixtureSubagentTranscript:
    def test_evolving_usage_keeps_last(self):
        events = _load_cc_fixture("glm-5.2-subagent-transcript.jsonl")
        samples = extract_cc_samples(
            events,
            native_session_id="s",
            main_or_subagent="subagent",
            window_resolver=force_window(200_000),
        )
        evolving = [s for s in samples if s.used_tokens == 7365 + 49600 + 337]
        assert evolving, "expected the final evolved usage to survive dedup"
