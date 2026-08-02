"""从 Electron Host 提供的隔离环境启动本地 Python sidecar。"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from trowel_py.desktop.data_compatibility import DesktopDataMode


@dataclass(frozen=True)
class DesktopSidecarSettings:
    """保存单次桌面应用实例启动 sidecar 所需的全部事实。

    Attributes:
        instance_id: Host 为本次应用启动生成的唯一实例 ID。
        credential: renderer 访问本实例 API 时使用的随机凭据。
        port: sidecar 只在 ``127.0.0.1`` 监听的私有端口。
        data_dir: 数据库和其他相对持久化路径使用的根目录。
        data_mode: 当前进程使用正式、日常开发还是隔离开发数据。
        log_dir: sidecar 生命周期日志写入的目录。
        renderer_origin: 本实例 renderer 发起跨来源 API 请求时使用的来源。
    """

    instance_id: str
    credential: str
    port: int
    data_dir: Path
    data_mode: DesktopDataMode
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
    raw_data_mode = environment.get("TROWEL_DESKTOP_DATA_MODE", "packaged").strip()
    if raw_data_mode not in {"packaged", "canonical-dev", "isolated-dev"}:
        raise ValueError("TROWEL_DESKTOP_DATA_MODE is invalid")

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
        data_mode=cast(DesktopDataMode, raw_data_mode),
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


def configure_desktop_data_environment(
    data_dir: Path,
    environment: MutableMapping[str, str],
) -> None:
    """让当前 sidecar 及其子进程使用 Host 指定的 Trowel 数据根目录。

    Args:
        data_dir: Desktop Host 为当前安装选择的应用数据根目录。
        environment: sidecar 与后续子进程共同继承的可变环境映射。

    Notes:
        本函数不会改写 ``HOME``、``CODEX_HOME`` 或 Claude Code 路径；这些目录
        属于 runtime，而不是 Trowel 应用数据。
    """
    environment["TROWEL_DATA_ROOT"] = str(data_dir)


def configure_desktop_runtime_path(
    home: Path,
    environment: MutableMapping[str, str],
    *,
    system_directories: tuple[Path, ...] = (
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    ),
) -> None:
    """补齐从 Finder 启动时可能缺失的 runtime CLI 搜索目录。

    Args:
        home: 当前 macOS 用户的主目录，只用于定位用户级 CLI 安装目录。
        environment: sidecar 与 runtime 子进程共同继承的可变环境映射。
        system_directories: 需要检查并放到现有 ``PATH`` 前面的系统级 CLI 目录。
    """
    if environment.get("TROWEL_RUNTIME_DISCOVERY_DISABLED") == "1":
        return

    candidates = (
        home / ".local" / "bin",
        home / ".bun" / "bin",
        home / ".npm-global" / "bin",
        *system_directories,
    )
    existing_entries = [
        entry for entry in environment.get("PATH", "").split(os.pathsep) if entry
    ]
    search_entries: list[str] = []
    for directory in (*candidates, *(Path(entry) for entry in existing_entries)):
        rendered = str(directory)
        if directory.is_dir() and rendered not in search_entries:
            search_entries.append(rendered)
    environment["PATH"] = os.pathsep.join(search_entries)


def run_sidecar(settings: DesktopSidecarSettings) -> None:
    """准备隔离目录、迁移数据库并阻塞运行本地 sidecar。

    Args:
        settings: Host 已完整指定并通过校验的本实例启动设置。
    """
    from trowel_py.desktop.data_compatibility import ensure_data_mode_compatible
    from trowel_py.desktop.data_root_lock import hold_data_root_lock

    ensure_data_mode_compatible(settings.data_dir, mode=settings.data_mode)
    with hold_data_root_lock(settings.data_dir, owner=settings.data_mode):
        ensure_data_mode_compatible(settings.data_dir, mode=settings.data_mode)
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        configure_desktop_data_environment(settings.data_dir, os.environ)
        configure_desktop_runtime_path(Path.home(), os.environ)
        _configure_logging(settings.log_dir)

        # 在发布数据根后再导入应用，确保模块按当前桌面实例解析持久化路径。
        import uvicorn

        from trowel_py.db.connection import create_db
        from trowel_py.db.migrate import run_migrations

        connection = create_db()
        try:
            run_migrations(connection)
        finally:
            connection.close()

        logging.getLogger(__name__).info(
            "Starting desktop sidecar instance=%s port=%s mode=%s",
            settings.instance_id,
            settings.port,
            settings.data_mode,
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
