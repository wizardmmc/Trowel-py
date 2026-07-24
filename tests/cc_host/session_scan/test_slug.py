from pathlib import Path

from trowel_py.cc_host import session_scan


def test_slash_becomes_dash() -> None:
    assert session_scan.workdir_to_slug("/workspace/project") == "-workspace-project"


def test_underscore_becomes_dash() -> None:
    assert (
        session_scan.workdir_to_slug("/workspace/my_research_project")
        == "-workspace-my-research-project"
    )


def test_dot_becomes_dash() -> None:
    assert session_scan.workdir_to_slug("/workspace/.config") == "-workspace--config"


def test_uppercase_and_digits_preserved() -> None:
    assert (
        session_scan.workdir_to_slug("/workspace/Papers/Tool2-Agent")
        == "-workspace-Papers-Tool2-Agent"
    )


def test_pathlike_input_matches_str() -> None:
    assert session_scan.workdir_to_slug(Path("/x/y_z")) == (
        session_scan.workdir_to_slug("/x/y_z")
    )


def test_symlink_resolved_to_match_cc(tmp_path: Path) -> None:
    real = tmp_path / "real_dir"
    real.mkdir()
    link = tmp_path / "link_dir"
    link.symlink_to(real)

    # CC 按 realpath 生成 slug，符号链接必须映射到同一目录。
    assert session_scan.workdir_to_slug(link) == session_scan.workdir_to_slug(real)
