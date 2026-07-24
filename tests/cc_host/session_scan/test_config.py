from pathlib import Path

from trowel_py.cc_host import session_scan

from .support import VALID_SESSION_ID, write_events


def test_read_session_config_uses_latest_confirmed_cc_facts(
    fake_projects: Path,
) -> None:
    # 字段路径来自 2026-07-24 本机真实 CC transcript；正文和身份字段已移除。
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [
            {
                "type": "system",
                "subtype": "init",
                "model": "glm-old",
            },
            {
                "type": "assistant",
                "message": {"model": "<synthetic>"},
            },
            {
                "type": "attachment",
                "attachment": {
                    "response": {
                        "effort": {"level": "medium"},
                        "permission_mode": "default",
                    }
                },
            },
            {
                "type": "assistant",
                "message": {"model": "glm-5.2"},
            },
            {
                "type": "user",
                "permissionMode": "bypassPermissions",
            },
            {
                "type": "attachment",
                "attachment": {
                    "response": {
                        "effort": {"level": "max"},
                        "permission_mode": "bypassPermissions",
                    }
                },
            },
        ],
    )

    config = session_scan.read_session_config("/workdir", VALID_SESSION_ID)

    assert config == session_scan.SessionConfigSummary(
        model="glm-5.2",
        effort="max",
        permission_mode="bypassPermissions",
    )


def test_read_session_config_rejects_non_uuid_and_missing_files(
    fake_projects: Path,
) -> None:
    del fake_projects
    assert session_scan.read_session_config("/workdir", "../escape") is None
    assert session_scan.read_session_config("/workdir", VALID_SESSION_ID) is None
