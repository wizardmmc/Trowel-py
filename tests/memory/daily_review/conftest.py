from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.memory.daily_review import support


@pytest.fixture(autouse=True)
def _review_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    support.prepare_default_jsonl(tmp_path / "default.jsonl")

    class FakeProvider:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            marker = "【segment "
            segment = "s1:0:end"
            index = user_prompt.find(marker)
            if index >= 0:
                segment = user_prompt[index + len(marker) :].split("】", 1)[0]
            return json.dumps(
                {
                    "items": [
                        {
                            "type": "outcome",
                            "text": "压缩版日记",
                            "source": segment,
                        }
                    ]
                }
            )

    # config 加载和 provider 都必须隔离，clean clone 没有私人 config.toml。
    monkeypatch.setattr("trowel_py.config.load_llm_config", lambda: object())
    monkeypatch.setattr(
        "trowel_py.llm.client.AnthropicProvider",
        lambda _config: FakeProvider(),
    )
