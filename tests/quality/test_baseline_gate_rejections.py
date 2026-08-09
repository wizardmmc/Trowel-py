"""用等价最小缺陷证明三项基础检查器会真实拒绝错误。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """运行一个预期失败的正式检查器并保留完整诊断。"""

    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_docstring_checker_rejects_the_historical_nested_function_gap(
    tmp_path: Path,
) -> None:
    """缺少嵌套函数说明时，正式 docstring 脚本必须返回非零。"""

    source = tmp_path / "usage.py"
    source.write_text(
        '"""模拟 Memory 统计模块。"""\n'
        "def judgement_source():\n"
        '    """构造统计来源。"""\n'
        "    def as_timestamp(value: str) -> str:\n"
        "        return value\n"
        "    return as_timestamp\n",
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "docstring_coverage.py"),
            str(source),
        ],
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert "function judgement_source.as_timestamp" in result.stdout
    assert "checked=1 missing=1" in result.stdout


def test_mypy_checker_rejects_a_strict_return_type_violation(
    tmp_path: Path,
) -> None:
    """返回值破坏显式类型契约时，仓库 mypy 配置必须返回非零。"""

    source = tmp_path / "typed.py"
    source.write_text(
        '"""模拟生产类型错误。"""\n'
        "def parse_count(value: str) -> int:\n"
        '    """把文本转换成数量。"""\n'
        "    return value\n",
        encoding="utf-8",
    )

    result = _run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--no-incremental",
            str(source),
        ],
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    assert "Incompatible return value type" in result.stdout
    assert "str" in result.stdout and "int" in result.stdout
