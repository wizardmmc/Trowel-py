"""验证进程树盘点只按 PPID 关系识别 Trowel 根进程的后代。"""

from __future__ import annotations

from subprocess import CompletedProcess

from trowel_py.resource_lifecycle import list_descendant_processes


def test_list_descendant_processes_follows_nested_parent_chain(monkeypatch) -> None:
    """同组、独立组和多层后代都应保留，无关进程必须排除。"""

    process_table = """
100 1 100 Sat Aug  2 00:00:00 2026 /usr/bin/codex
101 100 100 Sat Aug  2 00:00:01 2026 /usr/bin/git
102 100 102 Sat Aug  2 00:00:02 2026 /usr/bin/python
103 102 103 Sat Aug  2 00:00:03 2026 /usr/bin/sleep
200 1 200 Sat Aug  2 00:00:04 2026 /usr/bin/unrelated
"""

    monkeypatch.setattr(
        "trowel_py.resource_lifecycle.processes.subprocess.run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, process_table, ""),
    )

    descendants = list_descendant_processes(100)

    assert {item.pid for item in descendants} == {101, 102, 103}
    assert {item.process_group for item in descendants} == {100, 102, 103}
    assert all(item.start_identity for item in descendants)
