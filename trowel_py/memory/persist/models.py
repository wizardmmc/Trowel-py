"""定义提炼草稿持久化操作的结果契约。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PersistReport:
    """一次草稿持久化的写入计数、产物身份和完成状态。

    数值计数描述本次调用实际执行的写入；从完整 manifest 恢复的布尔值描述
    已有完成状态，不表示本次重新写入。数据类的冻结是浅层的：
    ``verification_counts`` 仍可原地修改，只是不能重新绑定该字段。

    Attributes:
        notes_written: 本次新建或更新的 Note 总数；幂等跳过时为 0。
        diary_written: 本次执行 Episode upsert 的次数；正常落盘为 1，幂等跳过
            为 0。
        verification_counts: 本次处理的 Note 按 verification 值统计的数量；
            幂等跳过时为空。
        notes_created: 正常落盘时本次新建的 Note ID；幂等跳过时为空。
        notes_updated: 正常落盘时本次更新的既有 Note ID；幂等跳过时为空。
        notes_skipped: 正常落盘时为空；幂等跳过时依次拼接原 manifest 的
            ``notes_created`` 和 ``notes_updated``，保留顺序和重复项。
        episode_written: 正常落盘和幂等跳过时均为 ``True``；后者表示恢复出的
            完成状态。
        reflection_written: 正常落盘时表示非空正文已写入；幂等跳过时表示
            manifest 的 ``reflection_file`` 不是 ``None``，空字符串也计为
            ``True``。
        escalation_written: 正常落盘时表示非空正文已写入；幂等跳过时表示
            manifest 的 ``escalation_file`` 不是 ``None``，空字符串也计为
            ``True``。
        manifest_path: 成功写入或复用的 completion manifest 相对于 Memory
            根目录的路径；默认构造且尚无完成 manifest 时为 ``None``。
        ok: completion manifest 已成功写入，或已有 manifest 通过完整性检查。
    """

    notes_written: int
    diary_written: int
    verification_counts: dict[str, int]
    notes_created: tuple[str, ...] = ()
    notes_updated: tuple[str, ...] = ()
    notes_skipped: tuple[str, ...] = ()
    episode_written: bool = False
    reflection_written: bool = False
    escalation_written: bool = False
    manifest_path: str | None = None
    ok: bool = False
