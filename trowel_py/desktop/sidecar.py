"""从 Electron Host 提供的隔离环境启动本地 Python sidecar。"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DesktopSidecarSettings:
    """保存单次桌面应用实例启动 sidecar 所需的全部事实。

    Attributes:
        instance_id: Host 为本次应用启动生成的唯一实例 ID。
        credential: renderer 访问本实例 API 时使用的随机凭据。
        port: sidecar 只在 ``127.0.0.1`` 监听的私有端口。
        data_dir: 数据库和其他相对持久化路径使用的根目录。
        log_dir: sidecar 生命周期日志写入的目录。
        renderer_origin: 本实例 renderer 发起跨来源 API 请求时使用的来源。
    """

    instance_id: str
    credential: str
    port: int
    data_dir: Path
    log_dir: Path
    renderer_origin: str


def load_sidecar_settings(environment: Mapping[str, str]) -> DesktopSidecarSettings:
    """校验并读取 Host 通过环境传入的 sidecar 启动设置。

    Args:
        environment: 当前子进程环境变量；调用方可传隔离映射进行测试。

    Returns:
        已完成必填项、端口和绝对目录校验的启动设置。

    Raises:
        ValueError: 缺少必填项，端口越界，或数据和日志目录不是绝对路径。
    """
    instance_id = _required_environment(environment, "TROWEL_APP_INSTANCE_ID")
    credential = _required_environment(environment, "TROWEL_DESKTOP_CREDENTIAL")
    raw_port = _required_environment(environment, "TROWEL_SERVER_PORT")
    data_dir = Path(
        _required_environment(environment, "TROWEL_DESKTOP_DATA_DIR")
    ).expanduser()
    log_dir = Path(
        _required_environment(environment, "TROWEL_DESKTOP_LOG_DIR")
    ).expanduser()
    renderer_origin = _required_environment(
        environment, "TROWEL_DESKTOP_RENDERER_ORIGIN"
    )

    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError("TROWEL_SERVER_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("TROWEL_SERVER_PORT must be between 1 and 65535")
    if not data_dir.is_absolute():
        raise ValueError("TROWEL_DESKTOP_DATA_DIR must be absolute")
    if not log_dir.is_absolute():
        raise ValueError("TROWEL_DESKTOP_LOG_DIR must be absolute")

    from trowel_py.desktop.access import validate_desktop_renderer_origin

    renderer_origin = validate_desktop_renderer_origin(renderer_origin)

    return DesktopSidecarSettings(
        instance_id=instance_id,
        credential=credential,
        port=port,
        data_dir=data_dir,
        log_dir=log_dir,
        renderer_origin=renderer_origin,
    )


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    """读取一个去除首尾空白后仍非空的必填环境变量。

    Args:
        environment: 查找变量使用的环境映射。
        name: 必须存在且非空的环境变量名称。

    Returns:
        去除首尾空白后的变量值。

    Raises:
        ValueError: 变量缺失或只包含空白。
    """
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def run_sidecar(settings: DesktopSidecarSettings) -> None:
    """准备隔离目录、迁移数据库并阻塞运行本地 sidecar。

    Args:
        settings: Host 已完整指定并通过校验的本实例启动设置。
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(settings.data_dir)
    _configure_logging(settings.log_dir)

    # 在切换数据目录后再导入应用，确保配置和相对持久化路径指向本实例目录。
    import uvicorn

    from trowel_py.db.connection import create_db
    from trowel_py.db.migrate import run_migrations

    connection = create_db()
    try:
        run_migrations(connection)
    finally:
        connection.close()

    logging.getLogger(__name__).info(
        "Starting desktop sidecar instance=%s port=%s",
        settings.instance_id,
        settings.port,
    )
    uvicorn.run(
        "trowel_py.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )


def _configure_logging(log_dir: Path) -> None:
    """把 sidecar 系统日志写入 Host 指定目录和标准输出。

    Args:
        log_dir: 当前应用实例允许写入日志的目录。
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(log_dir / "trowel.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> None:
    """从当前环境加载设置并启动 Electron 持有的 sidecar。"""
    try:
        settings = load_sidecar_settings(os.environ)
    except ValueError as exc:
        print(f"desktop sidecar configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    run_sidecar(settings)


if __name__ == "__main__":
    main()
