"""验证 Memory 统计保留独立分母、时间窗和来源质量。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.memory.north_star.support import hit, miss, write_note
from trowel_py.memory.access_log import AccessRecord, log_access
from trowel_py.memory.dictionary_state import DictionaryState, save_state
from trowel_py.memory.judgements import JudgementReport, save_judgement_report
from trowel_py.memory.north_star import memory_usage_metrics
from trowel_py.memory.sessions_repo import (
    SessionRecord,
    create_sessions_repository,
    open_sessions_db,
)
from trowel_py.memory.store import MemoryStore
from trowel_py.statistics.memory.repository import FileMemoryStatisticsReader
from trowel_py.statistics.memory.service import build_memory_statistics
from trowel_py.statistics.routes import router
from trowel_py.statistics.window import parse_statistics_window


def test_memory_statistics_keeps_three_denominators_independent(
    tmp_path: Path,
) -> None:
    stems = _seed_production_shape_memory(tmp_path)
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    data = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert data.retrieval.read_rate.numerator == 5
    assert data.retrieval.read_rate.denominator == 47
    assert data.effect.helpful_rate.numerator == 6
    assert data.effect.helpful_rate.denominator == 13
    assert data.recall.miss_rate.numerator == 6
    assert data.recall.miss_rate.denominator == 147
    assert data.effect.unknown == 1
    assert data.effect.helpful + data.effect.harmful + data.effect.unused == 13
    assert data.assets.active_notes == len(stems)
    assert data.assets.dictionary_status == "consistent"
    public_json = data.model_dump_json()
    assert "memory_id" not in public_json
    assert "session_id" not in public_json
    assert "统计样本" not in public_json


def test_memory_statistics_matches_existing_metrics_for_same_window(
    tmp_path: Path,
) -> None:
    _seed_production_shape_memory(tmp_path)
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    existing = memory_usage_metrics(
        tmp_path,
        local_tz=window.start.tzinfo,
        window_start=window.start,
        window_end=window.end,
    )
    data = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert data.retrieval.search_hits == existing["retrieval"]["search_hits"]
    assert data.retrieval.reads == existing["retrieval"]["reads"]
    assert data.effect.helpful == existing["effect"]["helpful_sessions"]
    assert data.effect.unknown == existing["effect"]["unknown_sessions"]
    assert data.recall.retrieval_miss == existing["recall"]["retrieval_miss"]


def test_memory_statistics_http_matches_facade_for_same_root(tmp_path: Path) -> None:
    """同一隔离 Memory 根经 facade 和 HTTP 公开相同的核心计数。"""
    _seed_production_shape_memory(tmp_path)
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )
    direct = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)
    app = FastAPI()
    app.state.memory_statistics_reader = FileMemoryStatisticsReader(tmp_path)
    app.include_router(router, prefix="/api/statistics")

    response = TestClient(app).get(
        "/api/statistics/memory",
        params={
            "start_date": "2026-08-02",
            "end_date": "2026-08-02",
            "timezone": "Asia/Shanghai",
        },
    )

    assert response.status_code == 200
    body = response.json()["data"]
    assert body["attribution"] == direct.attribution.model_dump(mode="json")
    assert body["retrieval"] == direct.retrieval.model_dump(mode="json")
    assert body["effect"] == direct.effect.model_dump(mode="json")
    assert body["recall"] == direct.recall.model_dump(mode="json")


def test_memory_statistics_counts_real_search_calls_and_empty_results(
    tmp_path: Path,
) -> None:
    """搜索调用、非空调用和空结果按 access log 的真实结构计数。"""
    stem = write_note(tmp_path, "search-call-note")
    _seed_sessions(tmp_path, 1)
    for search_id in ("with-hit", "empty"):
        log_access(
            tmp_path,
            AccessRecord(
                ts="2026-08-02T10:00:00+08:00",
                trowel_session_id="",
                cc_session_id="s000",
                toolUseId=f"tool-{search_id}",
                action="search",
                search_id=search_id,
                query="搜索调用样本",
            ),
        )
    log_access(
        tmp_path,
        AccessRecord(
            ts="2026-08-02T10:00:01+08:00",
            trowel_session_id="",
            cc_session_id="s000",
            toolUseId="tool-with-hit",
            action="search",
            search_id="with-hit",
            memory_id=stem,
            rank=0,
        ),
    )
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    data = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert data.retrieval.search_calls == 2
    assert data.retrieval.nonempty_search_calls == 1
    assert data.retrieval.empty_search_calls == 1
    assert data.retrieval.search_hits == 1


def test_memory_statistics_keeps_unknown_attribution_visible(
    tmp_path: Path,
) -> None:
    """没有任何会话身份的窗内记录只进入未归因计数。"""
    log_access(
        tmp_path,
        AccessRecord(
            ts="2026-08-02T10:00:00+08:00",
            trowel_session_id="",
            cc_session_id="",
            toolUseId="tool-unattributed",
            action="search",
            search_id="unattributed",
            query="未归因样本",
        ),
    )
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    data = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert data.attribution.attributed == 0
    assert data.attribution.unattributed == 1
    assert data.attribution.coverage.denominator == 1
    assert data.attribution.coverage.ratio == 0.0
    assert data.attribution.quality == "partial"
    assert data.retrieval.search_calls == 0


def test_memory_statistics_does_not_force_outside_access_into_window(
    tmp_path: Path,
) -> None:
    """有稳定时间但落在别日的记录只影响来源更新时间，不进入本窗样本。"""
    _seed_sessions(tmp_path, 1)
    log_access(
        tmp_path,
        AccessRecord(
            ts="2026-08-01T23:59:59+08:00",
            trowel_session_id="",
            cc_session_id="s000",
            toolUseId="tool-previous-day",
            action="search",
            search_id="previous-day",
            query="窗外样本",
        ),
    )
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    data = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert data.sample_size == 0
    assert data.sources["access"].sample_size == 0
    assert data.sources["access"].sample_start is None
    assert data.sources["access"].updated_at is not None


def test_memory_statistics_loads_note_snapshot_once(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """同一统计请求让 usage、effect、health 和来源元数据复用一次 Note 扫描。"""
    write_note(tmp_path, "single-snapshot-note")
    original_load = MemoryStore.load_notes_with_id
    calls = 0

    def counting_load(store: MemoryStore, filter=None):
        """记录测试请求触发的 Note 文件扫描次数。"""
        nonlocal calls
        calls += 1
        return original_load(store, filter)

    monkeypatch.setattr(MemoryStore, "load_notes_with_id", counting_load)
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert calls == 1


def test_legacy_judgement_is_not_forced_into_selected_date(tmp_path: Path) -> None:
    stem = write_note(tmp_path, "legacy-note")
    _seed_sessions(tmp_path, 1)
    save_judgement_report(
        tmp_path,
        JudgementReport(
            cc_session_id="s000",
            hits=(hit("legacy-note", "helpful"),),
            recall_miss=(),
            summary="旧记录没有活动日期",
        ),
    )
    _log_access(tmp_path, "s000", stem, "read", 0)
    window = parse_statistics_window(
        date(2026, 8, 2),
        date(2026, 8, 2),
        "Asia/Shanghai",
    )

    data = build_memory_statistics(FileMemoryStatisticsReader(tmp_path), window)

    assert data.effect.helpful == 0
    assert data.effect.quality == "partial"
    assert data.sources["judgements"].quality == "partial"
    assert data.freshness["judgements"].status == "unavailable"


def _seed_production_shape_memory(root: Path) -> list[str]:
    stable_ids = [f"note-{index}" for index in range(14)]
    stems = [write_note(root, memory_id) for memory_id in stable_ids]
    _seed_sessions(root, 147)
    for index in range(47):
        _log_access(root, "s000", stems[index % len(stems)], "search", index)
    for index in range(5):
        _log_access(root, "s000", stems[index], "read", index)
    for index in range(147):
        hits = ()
        if index < 6:
            hits = (hit(stable_ids[index], "helpful"),)
        elif index < 13:
            hits = (hit(stable_ids[index], "unused"),)
        elif index == 13:
            hits = (hit(stable_ids[index], "unknown"),)
        recall = ()
        if index < 3:
            recall = (miss(stable_ids[index], "retrieval_miss"),)
        elif index < 6:
            recall = (miss(stable_ids[index], "awareness_miss"),)
        save_judgement_report(
            root,
            JudgementReport(
                cc_session_id=f"s{index:03d}",
                hits=hits,
                recall_miss=recall,
                summary="脱敏判效样本",
                activity_dates=("2026-08-02",),
            ),
        )
    save_state(
        root,
        DictionaryState().with_success(
            "source-hash",
            "rendered-hash",
            "2026-08-02T15:30:00+08:00",
        ),
    )
    return stems


def _seed_sessions(root: Path, count: int) -> None:
    connection = open_sessions_db(root)
    try:
        repo = create_sessions_repository(connection).claude
        for index in range(count):
            repo.register(
                SessionRecord(
                    cc_session_id=f"s{index:03d}",
                    workdir="/isolated/project",
                    date="2026-08-02",
                    registered_at="2026-08-02T09:00:00+08:00",
                    session_kind="user",
                )
            )
    finally:
        connection.close()


def _log_access(
    root: Path,
    cc_session_id: str,
    stem: str,
    action: str,
    index: int,
) -> None:
    log_access(
        root,
        AccessRecord(
            ts="2026-08-02T10:00:00+08:00",
            trowel_session_id="",
            cc_session_id=cc_session_id,
            toolUseId=f"tool-{action}-{index}",
            action=action,  # type: ignore[arg-type]
            search_id="search-1",
            read_id=f"read-{index}" if action == "read" else "",
            query="统计样本" if action == "search" else "",
            memory_id=stem,
            rank=index if action == "search" else None,
        ),
    )
