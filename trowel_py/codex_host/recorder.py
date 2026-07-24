"""受环境变量控制的原始协议录制器。

录制默认关闭，启用后也必须先经 ``redact_message`` 脱敏再写盘。每行 JSONL 为
``{"t": <epoch>, "dir": "in"|"out", "msg": <redacted>}``；transport 负责调用
生命周期，recorder 只持有文件。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, BinaryIO

from trowel_py.codex_host.secrets import redact_message

RECORDER_ENV_FLAG = "TROWEL_CODEX_RECORD"


def recording_enabled(target: Path | str) -> bool:
    """检查目标路径是否被显式允许录制。

    ``TROWEL_CODEX_RECORD=1`` 等布尔值允许任意目标；路径值只允许完全匹配的
    recorder，避免测试意外开启同进程中的其他录制器。
    """

    flag = os.environ.get(RECORDER_ENV_FLAG, "").strip()
    if not flag:
        return False
    if flag in {"1", "true", "True", "yes"}:
        return True
    return Path(flag) == Path(target)


class RawRecorder:
    """启用后将脱敏协议追加到 JSONL。

    文件在首次写入时惰性打开；禁用状态下所有方法均为空操作，调用方无需另加分支。
    """

    def __init__(self, path: Path, *, clock: Any = time.time) -> None:
        self._path = path
        self._clock = clock
        self._handle: BinaryIO | None = None
        self._enabled = recording_enabled(path)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, direction: str, message: Any) -> None:
        """追加一条脱敏消息；``out`` 表示发往 server，``in`` 表示来自 server。"""

        if not self._enabled:
            return
        if self._handle is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self._path.open("ab")
        record = {
            "t": self._clock(),
            "dir": direction,
            "msg": redact_message(message),
        }
        self._handle.write(
            (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self._handle.flush()

    def close(self) -> None:
        """关闭已打开的文件；重复调用不产生副作用。"""

        if self._handle is not None:
            self._handle.close()
            self._handle = None
