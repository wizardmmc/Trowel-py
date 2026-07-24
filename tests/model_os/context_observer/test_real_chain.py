from __future__ import annotations

from trowel_py.model_os.context_observer import (
    ContextConfidence,
    cc_context_events_from_agent,
    extract_cc_samples,
)


class TestCcRealChain:
    def test_real_assistant_envelope_yields_context_sample(self):
        from trowel_py.cc_host.translator import Translator
        from trowel_py.agent_host.cc_adapter import CcEventAdapter

        raw_assistant = {
            "type": "assistant",
            "message": {
                "id": "msg_real_1",
                "model": "glm-5.2",
                "usage": {
                    "input_tokens": 50000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 100,
                },
                "content": [{"type": "text", "text": "ok"}],
            },
        }
        trowel_events = Translator().translate(raw_assistant)
        # 内容事件不再携带 usage，必须在拆分前单独产出 ContextUsageEvent。
        assert "ContextUsageEvent" in {type(e).__name__ for e in trowel_events}
        adapter = CcEventAdapter("s1")
        agent_events = [
            ae.model_dump(by_alias=True)
            for ae in (adapter.wrap(e.model_dump()) for e in trowel_events)
            if ae is not None
        ]
        normalized = cc_context_events_from_agent(agent_events)
        samples = extract_cc_samples(
            normalized,
            native_session_id="s1",
            main_or_subagent="main",
            source_version="2.1.197",
        )
        assert samples, "the real chain must yield at least one ContextSample"
        assert samples[0].used_tokens == 50100
        assert samples[0].confidence is ContextConfidence.RELIABLE
        assert samples[0].request_identity == "msg_real_1"
        assert samples[0].source_version == "2.1.197"

    def test_real_compact_boundary_carries_trigger(self):
        from trowel_py.cc_host.translator import Translator
        from trowel_py.agent_host.cc_adapter import CcEventAdapter

        raw_boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "auto", "preTokens": 167000},
        }
        trowel_events = Translator().translate(raw_boundary)
        adapter = CcEventAdapter("s1")
        agent_events = [
            ae.model_dump(by_alias=True)
            for ae in (adapter.wrap(e.model_dump()) for e in trowel_events)
            if ae is not None
        ]
        normalized = cc_context_events_from_agent(agent_events)
        assert any(
            e.type == "system"
            and e.subtype == "compact_boundary"
            and e.compact_trigger == "auto"
            for e in normalized
        )
