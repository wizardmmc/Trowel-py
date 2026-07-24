from __future__ import annotations

import trowel_py.memory.prompt as prompt_module
from trowel_py.memory.prompt import (
    DAILY_COMPRESS_TEMPLATE,
    DAILY_ITEMS_SCHEMA,
    build_daily_compress_prompt,
)


def test_daily_compress_prompt_requires_source_per_item() -> None:
    assert "source" in DAILY_COMPRESS_TEMPLATE
    assert "segment" in DAILY_COMPRESS_TEMPLATE


def test_daily_compress_prompt_states_budget_priority() -> None:
    assert "更正" in DAILY_COMPRESS_TEMPLATE
    assert "待续" in DAILY_COMPRESS_TEMPLATE
    assert "800" in DAILY_COMPRESS_TEMPLATE


def test_daily_items_schema_has_type_text_source() -> None:
    assert '"type"' in DAILY_ITEMS_SCHEMA
    assert '"text"' in DAILY_ITEMS_SCHEMA
    assert '"source"' in DAILY_ITEMS_SCHEMA


def test_daily_compress_prompt_bans_self_eval_and_flow() -> None:
    assert "自评" in DAILY_COMPRESS_TEMPLATE
    assert "工具调用" in DAILY_COMPRESS_TEMPLATE or "逐轮" in DAILY_COMPRESS_TEMPLATE
    assert "已解决" in DAILY_COMPRESS_TEMPLATE


def test_build_daily_compress_prompt_fills_placeholders() -> None:
    prompt = build_daily_compress_prompt(
        date="2026-07-17",
        sources_block="【seg-1】outcomes: 完成了 X",
    )

    assert "2026-07-17" in prompt
    assert "seg-1" in prompt
    assert "{date}" not in prompt
    assert "{sources_block}" not in prompt


def test_facade_daily_template_patch_flows_to_builder(monkeypatch) -> None:
    monkeypatch.setattr(
        prompt_module,
        "DAILY_COMPRESS_TEMPLATE",
        "date={date};sources={sources_block}",
    )

    prompt = build_daily_compress_prompt(date="2099-01-01", sources_block="S1")

    assert prompt == "date=2099-01-01;sources=S1"
