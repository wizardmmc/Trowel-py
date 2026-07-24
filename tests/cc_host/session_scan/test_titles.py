import json
from pathlib import Path

from trowel_py.cc_host import session_scan
from trowel_py.cc_host.session_scan import SessionSummary

from .support import (
    VALID_SESSION_ID,
    ai_title_event,
    metadata_events,
    user_event,
    write_events,
)


def test_title_from_first_prompt(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [user_event("帮我整理模块边界")],
    )

    sessions = session_scan.list_sessions("/workdir")

    assert len(sessions) == 1
    assert isinstance(sessions[0], SessionSummary)
    assert sessions[0].cc_session_id == VALID_SESSION_ID
    assert sessions[0].title == "帮我整理模块边界"


def test_title_prefers_aititle_over_first_prompt(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [user_event("原始消息"), ai_title_event("整理模块边界")],
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.title for session in sessions] == ["整理模块边界"]


def test_title_prefers_customtitle_over_aititle(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [
            user_event("原始消息"),
            ai_title_event("自动标题"),
            {
                "type": "user-custom-title",
                "customTitle": "自定义标题",
            },
        ],
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.title for session in sessions] == ["自定义标题"]


def test_aititle_in_tail_beyond_head_window(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [
            user_event("首条消息"),
            *metadata_events(1000),
            ai_title_event("尾部标题"),
        ],
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.title for session in sessions] == ["尾部标题"]


def test_tool_result_echo_not_used_as_title(fake_projects: Path) -> None:
    write_events(
        fake_projects / f"{VALID_SESSION_ID}.jsonl",
        [
            {
                "type": "user",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "tool output",
                        }
                    ],
                },
            },
            user_event("真正的首条用户消息"),
        ],
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.title for session in sessions] == ["真正的首条用户消息"]


def test_first_prompt_after_large_metadata_prefix_is_visible(
    fake_projects: Path,
) -> None:
    large_metadata = {
        "type": "attachment",
        "isSidechain": False,
        "data": "x" * 5000,
    }
    path = fake_projects / f"{VALID_SESSION_ID}.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "queue-operation", "data": "x" * 500}),
                json.dumps({"type": "queue-operation", "data": "x" * 500}),
                json.dumps(large_metadata),
                json.dumps(large_metadata),
                json.dumps(user_event("大段元数据后的首条消息"), ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    sessions = session_scan.list_sessions("/workdir")

    assert [session.title for session in sessions] == ["大段元数据后的首条消息"]
