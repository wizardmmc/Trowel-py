"""验证 Python sidecar 只从受控环境读取启动身份和数据目录。"""

from pathlib import Path

import pytest

from trowel_py.desktop.sidecar import (
    configure_desktop_data_environment,
    configure_desktop_runtime_path,
    load_sidecar_settings,
)


def test_load_sidecar_settings_reads_explicit_instance_directories(
    tmp_path: Path,
) -> None:
    """sidecar 启动参数必须完整指向本实例和隔离的数据目录。"""
    data_dir = tmp_path / "data"
    log_dir = tmp_path / "logs"

    settings = load_sidecar_settings(
        {
            "TROWEL_APP_INSTANCE_ID": "instance-123",
            "TROWEL_DESKTOP_CREDENTIAL": "desktop-secret",
            "TROWEL_SERVER_PORT": "43123",
            "TROWEL_DESKTOP_DATA_DIR": str(data_dir),
            "TROWEL_DESKTOP_LOG_DIR": str(log_dir),
            "TROWEL_DESKTOP_RENDERER_ORIGIN": "http://127.0.0.1:43124",
        }
    )

    assert settings.instance_id == "instance-123"
    assert settings.credential == "desktop-secret"
    assert settings.port == 43123
    assert settings.data_dir == data_dir
    assert settings.data_mode == "packaged"
    assert settings.log_dir == log_dir
    assert settings.renderer_origin == "http://127.0.0.1:43124"
    assert settings.inspection_only is False
    assert settings.read_data_dir is None


def test_load_sidecar_settings_requires_explicit_read_root_for_inspection(
    tmp_path: Path,
) -> None:
    """只读观察实例必须把临时运行目录和真实读取目录分开。"""

    base = {
        "TROWEL_APP_INSTANCE_ID": "instance-123",
        "TROWEL_DESKTOP_CREDENTIAL": "desktop-secret",
        "TROWEL_SERVER_PORT": "43123",
        "TROWEL_DESKTOP_DATA_DIR": str(tmp_path / "temporary-data"),
        "TROWEL_DESKTOP_LOG_DIR": str(tmp_path / "logs"),
        "TROWEL_DESKTOP_RENDERER_ORIGIN": "http://127.0.0.1:43124",
        "TROWEL_DESKTOP_DATA_MODE": "isolated-dev",
        "TROWEL_DESKTOP_INSPECTION_ONLY": "1",
    }

    with pytest.raises(ValueError, match="TROWEL_DESKTOP_READ_DATA_DIR"):
        load_sidecar_settings(base)

    read_root = tmp_path / "canonical-data"
    settings = load_sidecar_settings(
        {**base, "TROWEL_DESKTOP_READ_DATA_DIR": str(read_root)}
    )

    assert settings.inspection_only is True
    assert settings.read_data_dir == read_root
    assert settings.data_dir != settings.read_data_dir


@pytest.mark.parametrize(
    "missing_key",
    [
        "TROWEL_APP_INSTANCE_ID",
        "TROWEL_DESKTOP_CREDENTIAL",
        "TROWEL_SERVER_PORT",
        "TROWEL_DESKTOP_DATA_DIR",
        "TROWEL_DESKTOP_LOG_DIR",
        "TROWEL_DESKTOP_RENDERER_ORIGIN",
    ],
)
def test_load_sidecar_settings_rejects_missing_identity(
    tmp_path: Path,
    missing_key: str,
) -> None:
    """缺少任一 Host 生成的启动字段时，sidecar 必须拒绝运行。"""
    environment = {
        "TROWEL_APP_INSTANCE_ID": "instance-123",
        "TROWEL_DESKTOP_CREDENTIAL": "desktop-secret",
        "TROWEL_SERVER_PORT": "43123",
        "TROWEL_DESKTOP_DATA_DIR": str(tmp_path / "data"),
        "TROWEL_DESKTOP_LOG_DIR": str(tmp_path / "logs"),
        "TROWEL_DESKTOP_RENDERER_ORIGIN": "http://127.0.0.1:43124",
    }
    del environment[missing_key]

    with pytest.raises(ValueError, match=missing_key):
        load_sidecar_settings(environment)


def test_configure_desktop_data_environment_preserves_runtime_home(
    tmp_path: Path,
) -> None:
    """sidecar 只发布 Trowel 数据根目录，不能覆盖 runtime 使用的 ``HOME``。"""
    data_dir = tmp_path / "data"
    environment = {
        "HOME": str(tmp_path / "runtime-home"),
        "TROWEL_DESKTOP_DATA_DIR": str(data_dir),
    }

    configure_desktop_data_environment(data_dir, environment)

    assert environment["TROWEL_DATA_ROOT"] == str(data_dir)
    assert environment["HOME"] == str(tmp_path / "runtime-home")


def test_configure_desktop_runtime_path_adds_user_and_system_cli_directories(
    tmp_path: Path,
) -> None:
    """Finder 启动的 sidecar 也要能找到用户已安装的 runtime CLI。"""
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    bun_bin = home / ".bun" / "bin"
    homebrew_bin = tmp_path / "homebrew" / "bin"
    for directory in (local_bin, bun_bin, homebrew_bin):
        directory.mkdir(parents=True)
    environment = {"HOME": str(home), "PATH": "/usr/bin:/bin"}

    configure_desktop_runtime_path(
        home,
        environment,
        system_directories=(homebrew_bin,),
    )

    search_path = environment["PATH"].split(":")
    assert search_path == [
        str(local_bin),
        str(bun_bin),
        str(homebrew_bin),
        "/usr/bin",
        "/bin",
    ]
    assert environment["HOME"] == str(home)


def test_configure_desktop_runtime_path_can_be_disabled_for_isolated_smoke(
    tmp_path: Path,
) -> None:
    """整包隔离测试不得重新发现并启动开发机上的真实 runtime。"""

    environment = {
        "PATH": "/usr/bin:/bin",
        "TROWEL_RUNTIME_DISCOVERY_DISABLED": "1",
    }

    configure_desktop_runtime_path(tmp_path, environment)

    assert environment["PATH"] == "/usr/bin:/bin"
