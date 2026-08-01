"""验证 Python sidecar 只从受控环境读取启动身份和数据目录。"""

from pathlib import Path

import pytest

from trowel_py.desktop.sidecar import load_sidecar_settings


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
    assert settings.log_dir == log_dir
    assert settings.renderer_origin == "http://127.0.0.1:43124"


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
