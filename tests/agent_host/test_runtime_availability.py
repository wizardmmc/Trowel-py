"""验证 runtime CLI 安装探测只报告当前系统真正可执行的程序。"""

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.runtime_availability import detect_runtime_availability


def test_detect_runtime_availability_checks_both_cli_names() -> None:
    requested: list[str] = []

    def find_executable(name: str) -> str | None:
        requested.append(name)
        return "/usr/local/bin/claude" if name == "claude" else None

    availability = detect_runtime_availability(find_executable)

    assert availability == {
        Runtime.CLAUDE_CODE: True,
        Runtime.CODEX: False,
    }
    assert requested == ["claude", "codex"]
