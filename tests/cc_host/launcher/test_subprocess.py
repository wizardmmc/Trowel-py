from asyncio import subprocess as asubprocess
from pathlib import Path

from trowel_py.cc_host.launcher import build_subprocess_kwargs

WORKDIR = Path("/workspace/project")


def test_cwd_is_workdir_and_new_session() -> None:
    kwargs = build_subprocess_kwargs(workdir=WORKDIR)

    assert kwargs == {
        "cwd": str(WORKDIR),
        "stdin": asubprocess.PIPE,
        "stdout": asubprocess.PIPE,
        "stderr": asubprocess.PIPE,
        "start_new_session": True,
        "limit": 16 * 1024 * 1024,
    }


def test_kwargs_include_large_readline_limit() -> None:
    kwargs = build_subprocess_kwargs(workdir=WORKDIR)

    # CC 的单行 stream-json 可能超过 asyncio 默认的 64KB。
    assert kwargs["limit"] == 16 * 1024 * 1024


def test_env_arg_propagated_to_kwargs() -> None:
    kwargs = build_subprocess_kwargs(workdir=WORKDIR, env={"TEST_VAR": "value"})

    assert kwargs["env"] == {"TEST_VAR": "value"}


def test_env_none_omits_env_key() -> None:
    kwargs = build_subprocess_kwargs(workdir=WORKDIR)

    # 省略 env 才会让 create_subprocess_exec 完整继承父进程环境。
    assert "env" not in kwargs
