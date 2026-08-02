"""防止 Electron Host 与 Python sidecar 的应用版本静默漂移。"""

import json
from pathlib import Path

from trowel_py.desktop.contract import app_version


def test_electron_and_python_app_versions_match() -> None:
    """版本握手两端必须从各自发布清单读到同一个应用版本。"""
    package_path = Path(__file__).parents[2] / "web" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))

    assert package["version"] == app_version()
