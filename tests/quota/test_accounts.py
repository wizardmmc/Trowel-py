from __future__ import annotations

from trowel_py.quota.scheduler import GlmAccount, load_glm_accounts


class _FakeConfig:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url
        self.api_key = api_key


def test_placeholder_key_returns_empty() -> None:
    assert (
        load_glm_accounts(
            _FakeConfig("https://open.bigmodel.cn/api/anthropic", "<your-bigmodel-key>")
        )
        == []
    )


def test_real_bigmodel_key_returns_one_account_with_host_stripped() -> None:
    accounts = load_glm_accounts(
        _FakeConfig("https://open.bigmodel.cn/api/anthropic", "real.key")
    )
    assert len(accounts) == 1
    assert isinstance(accounts[0], GlmAccount)
    assert accounts[0].account_id == "glm"
    assert accounts[0].api_key == "real.key"
    assert accounts[0].host == "https://open.bigmodel.cn"


def test_non_bigmodel_base_url_returns_empty() -> None:
    assert load_glm_accounts(_FakeConfig("https://api.openai.com/v1", "sk-x")) == []


def test_deceptive_lookalike_hostname_is_rejected() -> None:
    assert (
        load_glm_accounts(
            _FakeConfig(
                "https://open.bigmodel.cn.attacker.invalid/api/anthropic", "real.key"
            )
        )
        == []
    )


def test_non_https_scheme_is_rejected() -> None:
    assert (
        load_glm_accounts(
            _FakeConfig("http://open.bigmodel.cn/api/anthropic", "real.key")
        )
        == []
    )
