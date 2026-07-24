from pathlib import Path

from trowel_py.cc_host.launcher import CLAUDE_BIN, build_args

WORKDIR = Path("/workspace/project")


def test_default_args_shape() -> None:
    assert build_args(workdir=WORKDIR) == [
        CLAUDE_BIN,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
        "--permission-prompt-tool",
        "stdio",
    ]


def test_fallback_model_override() -> None:
    args = build_args(workdir=WORKDIR, fallback_model="fallback-model")

    index = args.index("--fallback-model")

    assert args[index + 1] == "fallback-model"


def test_effort_override() -> None:
    args = build_args(workdir=WORKDIR, effort="high")

    index = args.index("--effort")

    assert args[index + 1] == "high"


def test_model_override() -> None:
    args = build_args(workdir=WORKDIR, model="test-model")

    index = args.index("--model")

    assert args[index + 1] == "test-model"


def test_resume_appends_resume_flag() -> None:
    args = build_args(workdir=WORKDIR, resume_from="session-id")

    index = args.index("--resume")

    assert args[index + 1] == "session-id"


def test_permission_mode_passthrough() -> None:
    args = build_args(workdir=WORKDIR, permission_mode="default")

    index = args.index("--permission-mode")

    assert args[index + 1] == "default"


def test_workdir_is_not_in_args() -> None:
    args = build_args(workdir=WORKDIR)

    # workdir 只能作为子进程 cwd，不能扩大为 --add-dir 权限。
    assert "--add-dir" not in args
    assert str(WORKDIR) not in args


def test_default_includes_permission_prompt_tool_stdio() -> None:
    args = build_args(workdir=WORKDIR)

    index = args.index("--permission-prompt-tool")

    assert args[index + 1] == "stdio"


def test_permission_prompt_tool_can_be_disabled() -> None:
    args = build_args(workdir=WORKDIR, permission_prompt_tool=None)

    assert "--permission-prompt-tool" not in args


def test_append_system_prompt_added() -> None:
    prompt = "# 记忆\n1. 先检索相关记录"
    args = build_args(workdir=WORKDIR, append_system_prompt=prompt)

    index = args.index("--append-system-prompt")

    assert args[index + 1] == prompt


def test_append_system_prompt_none_omitted() -> None:
    args = build_args(workdir=WORKDIR)

    assert "--append-system-prompt" not in args


def test_append_system_prompt_empty_omitted() -> None:
    args = build_args(workdir=WORKDIR, append_system_prompt="")

    assert "--append-system-prompt" not in args


def test_mcp_config_added_with_strict_mode() -> None:
    args = build_args(workdir=WORKDIR, mcp_config="/tmp/memory-mcp.json")

    index = args.index("--mcp-config")

    assert args[index : index + 3] == [
        "--mcp-config",
        "/tmp/memory-mcp.json",
        "--strict-mcp-config",
    ]


def test_mcp_config_coexists_with_system_prompt() -> None:
    args = build_args(
        workdir=WORKDIR,
        mcp_config="/tmp/memory-mcp.json",
        append_system_prompt="# 记忆",
    )

    assert "--mcp-config" in args
    assert "--strict-mcp-config" in args
    assert "--append-system-prompt" in args
