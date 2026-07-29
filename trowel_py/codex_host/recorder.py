"""受环境变量控制的 Codex app-server 协议录制器。

这里的“原始”消息指翻译为统一事件前的入站和出站载荷。写盘前会经
``redact_message`` 遮盖可识别的凭据，但会话正文和本机路径仍可能保留，因此
录制文件仍是敏感数据。每行 JSONL 的结构为
``{"t": <Unix 秒时间戳>, "dir": "in"|"out", "msg": <redacted>}``。首次启用的
``record()`` 调用会创建父目录，并以追加模式打开或创建文件；transport 在连接
关闭时调用 ``close()``。
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
    """根据环境变量判断目标文件是否允许录制。

    ``TROWEL_CODEX_RECORD`` 未设置或为空时禁用录制；值为 ``1``、``true``、
    ``True`` 或 ``yes`` 时允许任意目标，其他非空值只允许与目标按 ``Path``
    词法比较相等的路径，不解析绝对路径或符号链接。路径限定可避免测试意外开启
    同一进程中的其他录制器。

    Args:
        target: 待写入的 JSONL 文件。

    """

    flag = os.environ.get(RECORDER_ENV_FLAG, "").strip()
    if not flag:
        return False
    if flag in {"1", "true", "True", "yes"}:
        return True
    return Path(flag) == Path(target)


class RawRecorder:
    """将翻译前的协议消息脱敏后追加到 JSONL。

    是否启用在构造时确定，后续环境变量变化不会影响实例。首次启用的
    ``record()`` 调用才会打开文件；禁用时 ``record()`` 和 ``close()`` 均为空
    操作，不创建目录或文件。两个方法都不捕获异常，异常会向调用方传播。
    """

    def __init__(self, path: Path, *, clock: Any = time.time) -> None:
        """配置追加目标，并读取一次录制开关。

        Args:
            path: JSONL 追加写入路径；父目录在首次启用的 ``record()`` 调用时创建。
            clock: 为每条记录提供 Unix 时间戳的无参数调用对象。
        """

        self._path = path
        self._clock = clock
        self._handle: BinaryIO | None = None
        self._enabled = recording_enabled(path)

    @property
    def enabled(self) -> bool:
        """返回构造实例时读取到的录制开关。"""

        return self._enabled

    def record(self, direction: str, message: Any) -> None:
        """脱敏并追加一条消息，随后立即刷新文件缓冲区。

        Args:
            direction: 消息方向；``out`` 表示发往 app-server，``in`` 表示来自
                app-server。
            message: 待录制的原始协议载荷；本方法先脱敏，再写入 ``msg`` 字段。
        """

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
        """关闭当前文件句柄；可重复调用，后续写入会以追加模式重新打开文件。"""

        if self._handle is not None:
            self._handle.close()
            self._handle = None
