"""验证 Codex 原生账号接口只返回脱敏摘要和登录引导。"""

from __future__ import annotations

import asyncio

from tests.codex_host._fake import FakeAppServer, Step
from tests.codex_host.manager.support import _init_resp, _manager


async def test_account_read_and_device_login_follow_recorded_protocol() -> None:
    """账号读取与登录请求使用 Codex 0.144.0 app-server 的真实字段。"""

    async def behavior():
        initialize = yield Step.recv()
        yield _init_resp(initialize["id"])
        yield Step.recv()
        account = yield Step.recv()
        assert account["method"] == "account/read"
        assert account["params"] == {"refreshToken": False}
        yield Step.send(
            {
                "id": account["id"],
                "result": {
                    "account": {
                        "type": "chatgpt",
                        "email": "user@example.com",
                        "planType": "pro",
                    },
                    "requiresOpenaiAuth": True,
                },
            }
        )
        login = yield Step.recv()
        assert login["method"] == "account/login/start"
        assert login["params"] == {"type": "chatgptDeviceCode"}
        yield Step.send(
            {
                "id": login["id"],
                "result": {
                    "type": "chatgptDeviceCode",
                    "loginId": "login-1",
                    "verificationUrl": "https://auth.openai.com/codex/device",
                    "userCode": "ABCD-1234",
                },
            }
        )
        yield Step.send(
            {
                "method": "account/login/completed",
                "params": {"loginId": "login-1", "success": True, "error": None},
            }
        )
        refreshed = yield Step.recv()
        assert refreshed["method"] == "account/read"
        yield Step.send(
            {
                "id": refreshed["id"],
                "result": {
                    "account": {
                        "type": "chatgpt",
                        "email": "user@example.com",
                        "planType": "pro",
                    },
                    "requiresOpenaiAuth": True,
                },
            }
        )

    manager = _manager(FakeAppServer(behavior()))

    account = await manager.read_account()
    login = await manager.start_account_login()
    await asyncio.sleep(0)
    refreshed = await manager.read_account()

    assert account == {
        "status": "logged_in",
        "auth_mode": "chatgpt",
        "email": "user@example.com",
        "plan_type": "pro",
    }
    assert login["user_code"] == "ABCD-1234"
    assert refreshed["login_id"] == "login-1"
    assert refreshed["login_status"] == "completed"
    await manager.close()
