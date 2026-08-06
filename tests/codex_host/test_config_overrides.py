"""验证 Codex app-server 的结构化连接覆盖参数。"""

from trowel_py.codex_host.config_overrides import app_server_args


def test_nested_provider_config_is_flattened_after_app_server() -> None:
    """第三方 provider 的 env_key 与认证开关必须成为精确 dotted override。"""

    args = app_server_args(
        {
            "model_provider": "trowel_connection",
            "disable_response_storage": True,
            "model_providers": {
                "trowel_connection": {
                    "base_url": "https://api.example/v1",
                    "wire_api": "responses",
                    "env_key": "TROWEL_CODEX_PROVIDER_KEY",
                    "requires_openai_auth": False,
                }
            },
        }
    )

    assert args[:4] == (
        "-c",
        'model_provider="trowel_connection"',
        "-c",
        "disable_response_storage=true",
    )
    assert (
        "model_providers.trowel_connection.requires_openai_auth=false" in args
    )
    assert not any("secret" in item for item in args)
