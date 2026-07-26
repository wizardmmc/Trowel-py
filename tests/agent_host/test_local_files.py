from pathlib import Path

from trowel_py.agent_host.local_files import open_local_file


def test_open_file_handle_is_stable_after_path_becomes_an_external_symlink(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    target = workdir / "report.html"
    target.write_text("safe", encoding="utf-8")
    outside = tmp_path / "secret.html"
    outside.write_text("secret", encoding="utf-8")

    handle = open_local_file(str(workdir), "report.html")
    target.rename(workdir / "original.html")
    target.symlink_to(outside)

    with handle:
        assert handle.read() == b"safe"
