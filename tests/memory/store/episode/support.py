from trowel_py.memory.draft import DraftDiary
from trowel_py.memory.types import PersistContext


def _ctx(
    sid: str,
    *,
    review_date: str = "2026-07-09",
    workdir: str = "/proj",
    registered_at: str = "2026-07-09T10:00:00",
    segment_id: str | None = None,
    activity_dates: tuple[str, ...] = (),
    date_basis: str = "",
    processed_date: str = "",
) -> PersistContext:
    return PersistContext(
        segment_id=segment_id or f"{sid}:0:end",
        cc_session_id=sid,
        workdir=workdir,
        registered_at=registered_at,
        review_date=review_date,
        source_jsonl=f"/jsonl/{sid}.jsonl",
        activity_dates=activity_dates,
        date_basis=date_basis,
        processed_date=processed_date,
    )


def _structured_entry(date: str = "2026-07-17") -> DraftDiary:
    return DraftDiary(
        date=date,
        outcomes=("完成了 daily 重写", "验证到全量测试通过"),
        decisions=("固定三问结构：进展 / 更正 / 待续",),
        corrections=("原来以为单 $ 零误伤 -> 实测就近配对吞整段",),
        open_loops=("weekly 表达重写未做",),
    )
