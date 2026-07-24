from __future__ import annotations

from tests.model_os.context_observer.support import _load_codex_fixture
from trowel_py.model_os.context_observer import (
    cc_context_events_from_agent,
    codex_context_events_from_agent,
    extract_cc_samples,
    extract_codex_samples,
)


class TestAgentEventAdapter:
    def test_codex_usage_and_compaction_from_agent_events(self):
        events = [
            {
                "type": "usage_updated",
                "turn_id": "t1",
                "payload": {
                    "last": {"totalTokens": 15111},
                    "total": {"totalTokens": 15111},
                    "model_context_window": 258400,
                },
            },
            {
                "type": "compaction",
                "turn_id": "t1",
                "payload": {"phase": "completed"},
            },
            {
                "type": "usage_updated",
                "turn_id": "t2",
                "payload": {
                    "last": {"totalTokens": 15160},
                    "total": {"totalTokens": 30271},
                    "model_context_window": 258400,
                },
            },
        ]
        normalized = codex_context_events_from_agent(events)
        samples = extract_codex_samples(normalized, native_session_id="th")
        assert [s.generation for s in samples] == [0, 1]
        assert samples[0].used_tokens == 15111
        assert samples[1].used_tokens == 15160

    def test_cc_context_usage_and_boundary_from_agent_events(self):
        events = [
            {
                "type": "context_usage",
                "turn_id": "t1",
                "payload": {
                    "model": "glm-5.2",
                    "message_id": "m1",
                    "timestamp": "2026-07-22T00:00:00Z",
                    "usage": {
                        "input_tokens": 50000,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 100,
                    },
                },
            },
            {
                "type": "compact_boundary",
                "payload": {"trigger": "auto"},
            },
            {
                "type": "context_usage",
                "turn_id": "t2",
                "payload": {
                    "model": "glm-5.2",
                    "message_id": "m2",
                    "usage": {
                        "input_tokens": 30000,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 50,
                    },
                },
            },
        ]
        normalized = cc_context_events_from_agent(events)
        samples = extract_cc_samples(
            normalized,
            native_session_id="s",
            main_or_subagent="main",
        )
        assert [s.generation for s in samples] == [0, 1]
        assert samples[0].used_tokens == 50100


class TestCodexFixture:
    def test_usage_compact_sequence_matches_083(self):
        events = _load_codex_fixture("usage_compact_sequence.jsonl")
        normalized = codex_context_events_from_agent(events)
        samples = extract_codex_samples(
            normalized,
            native_session_id="th",
            source_version="0.144.0",
        )
        assert [s.generation for s in samples] == [0, 1, 1]
        assert [s.used_tokens for s in samples] == [15111, 4495, 15160]
        assert all(s.effective_window_tokens == 258400 for s in samples)
        assert all(s.used_tokens != 30271 for s in samples)

    def test_fresh_new_session_starts_generation_zero(self):
        events = _load_codex_fixture("usage_compact_sequence.jsonl")
        s1 = extract_codex_samples(
            codex_context_events_from_agent(events),
            native_session_id="th1",
        )
        s2 = extract_codex_samples(
            codex_context_events_from_agent(events),
            native_session_id="th2",
        )
        assert s1[0].generation == 0 and s2[0].generation == 0
        assert s1[0].native_session_id == "th1"
        assert s2[0].native_session_id == "th2"
