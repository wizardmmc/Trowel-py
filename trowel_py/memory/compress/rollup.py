"""编排 Weekly 与 Monthly 的缓存复用、生成校验、来源记录和派生文件写入。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from trowel_py.llm.client import LLMProvider
from trowel_py.memory.provenance import (
    DerivationProvenance,
    ModelIdentity,
    derivation_to_dict,
)
from trowel_py.memory.store import MemoryStore, _dump_frontmatter, _split_frontmatter

from .monthly_generation import (
    INPUT_BUDGET,
    MONTHLY_BUDGET,
    MONTHLY_GENERATION_VERSION,
    MONTHLY_SYSTEM_PROMPT,
    MONTHLY_USER_PROMPT,
    generate_monthly,
)
from .rollup_sources import (
    diary_path,
    in_iso_week,
    monthly_sources as monthly_sources,
    parse_iso_week,
    source_hash as source_hash,
    week_in_month,
    weekly_sources as weekly_sources,
)
from .weekly_generation import (
    BYPASS_CATEGORIES as BYPASS_CATEGORIES,
    WEEKLY_BUDGET,
    WEEKLY_GENERATION_VERSION,
    WeeklyBypassItem,
    WeeklySource,
    bypass_item_to_dict,
    generate_weekly,
    render_bypass,
    render_weekly,
    select_bypass_items,
    select_weekly_items,
    weekly_item_to_dict,
)

logger = logging.getLogger("trowel_py.memory.compress")

_INPUT_CAP = INPUT_BUDGET
_OUTPUT_CAP = MONTHLY_BUDGET
_WEEKLY_SYS = "weekly-v3 structured generation"
_WEEKLY_USER = "weekly-v3 sources: {body}"
_MONTHLY_GENERATION_VERSION = MONTHLY_GENERATION_VERSION
_MONTHLY_SYS = MONTHLY_SYSTEM_PROMPT
_MONTHLY_USER = MONTHLY_USER_PROMPT


def _cap(text: str) -> str:
    """将超出 Monthly 输出预算的文本截断并补省略号，供兼容调用使用。"""
    return text if len(text) <= _OUTPUT_CAP else text[: _OUTPUT_CAP - 1].rstrip() + "…"


def _parse_iso_week(s: str) -> tuple[int, int]:
    """解析并校验 ``YYYY-Www`` 格式的 ISO 周标识。"""
    return parse_iso_week(s)


def _in_iso_week(date_str: str, iso_year: int, iso_week: int) -> bool:
    """判断 ISO 日期字符串是否属于指定 ISO 年和周；格式无效时返回 False。"""
    return in_iso_week(date_str, iso_year, iso_week)


def _week_in_month(iso_week_str: str, month: str) -> bool:
    """判断 ISO 周的周一是否落在目标月份；格式无效时返回 False。"""
    return week_in_month(iso_week_str, month)


def _diary_path(root: Path, layer: str, period: str) -> Path:
    """返回指定层级和周期的派生日记路径。"""
    return diary_path(root, layer, period)


def _new_derivation(
    pipeline: str,
    pipeline_version: int,
    provider: LLMProvider,
    *,
    generated_at: str,
    run_id: str | None,
) -> dict[str, Any]:
    """构造一次 Weekly 或 Monthly 压缩的来源记录。

    Args:
        pipeline: 生成流水线名称。
        pipeline_version: 本次使用的流水线版本。
        provider: 直接调用的模型客户端。从 ``_model`` 读取模型，并从类名推导
            provider；没有 ``_model`` 时不记录 generator。
        generated_at: 产物生成时间。
        run_id: 重生成任务 ID；直接调用时为 None，并在此生成随机 ID。

    Returns:
        可写入 Diary frontmatter 的来源记录。
    """
    model = str(getattr(provider, "_model", "") or "").strip()
    provider_name = provider.__class__.__name__.removesuffix("Provider").lower()
    generator = (
        ModelIdentity(
            model=model,
            provider=provider_name,
            basis="host_config",
        )
        if model
        else None
    )
    return derivation_to_dict(
        DerivationProvenance(
            pipeline=pipeline,
            pipeline_version=pipeline_version,
            run_id=run_id or uuid.uuid4().hex,
            generated_at=generated_at,
            generator_runtime="direct_api",
            generator=generator,
        )
    )


def _existing_current(
    path: Path,
    *,
    expected_hash: str,
    expected_version: int,
    budget: int,
) -> bool:
    """判断现有派生物是否可作为当前来源的缓存复用。

    Args:
        path: 要检查的 Weekly 或 Monthly 文件。
        expected_hash: 当前上游来源的内容哈希。
        expected_version: 当前生成器版本。
        budget: 正文允许的最大字符数。
    """
    if not path.is_file():
        return False
    frontmatter, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    return bool(
        frontmatter
        and frontmatter.get("generation_status") == "ok"
        and frontmatter.get("source_hash") == expected_hash
        and frontmatter.get("generation_version") == expected_version
        and len(body.strip()) <= budget
    )


def compress_weekly(
    root: Path | str,
    iso_week: str,
    provider: LLMProvider,
    *,
    force: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """把目标 ISO 周的全部 Daily 压缩为 Weekly 及三类 bypass。

    现有 Weekly 的成功状态、来源哈希、生成版本和正文预算均有效时默认直接复用。
    新 Weekly 只有在预算内覆盖全部来源日期，以及 Daily 已有的“进展 / 更正 /
    待续”分区时才会写入。新 Weekly 校验通过并写入后，无条目的 bypass 类别会
    删除已有文件；生成失败或缓存命中不会清理 bypass。

    Args:
        root: memory 数据目录。
        iso_week: 目标 ISO 周，格式为 ``YYYY-Www``。
        provider: 生成结构化 Weekly 与 bypass 条目的模型客户端。
        force: 是否跳过缓存命中检查并重新生成。
        run_id: 写入来源记录的重生成任务 ID；直接调用时为 None。

    Returns:
        包含 ``weekly_written``、生成状态及各类 bypass 结果的报告。
        ``weekly_written`` 在新写入成功或缓存命中时为 True。``bypass`` 在缓存
        命中时表示对应文件是否存在，在新生成成功时表示本次是否写入；失败分支
        统一为 False，即使旧文件仍被保留。新生成成功时还包含 ``source_hash``
        和 ``selected_source_days``；预算或日期覆盖校验失败时包含
        ``missing_source_days``。
    """
    root_path = Path(root)
    sources = weekly_sources(root_path, iso_week)
    if not sources:
        return {
            "weekly_written": False,
            "iso_week": iso_week,
            "generation_status": "missing-source",
            "bypass": {category: False for category in BYPASS_CATEGORIES},
        }
    current_hash = source_hash(sources)
    weekly_path = _diary_path(root_path, "week", iso_week)
    if not force and _existing_current(
        weekly_path,
        expected_hash=current_hash,
        expected_version=WEEKLY_GENERATION_VERSION,
        budget=WEEKLY_BUDGET,
    ):
        return {
            "weekly_written": True,
            "iso_week": iso_week,
            "generation_status": "ok",
            "cached": True,
            "bypass": {
                category: (
                    root_path
                    / "diary"
                    / "bypass"
                    / category
                    / f"{iso_week}.md"
                ).is_file()
                for category in BYPASS_CATEGORIES
            },
        }

    generation = generate_weekly(
        provider,
        [WeeklySource(source.period, source.body) for source in sources],
    )
    if generation is None:
        return {
            "weekly_written": False,
            "iso_week": iso_week,
            "generation_status": "failed",
            "bypass": {category: False for category in BYPASS_CATEGORIES},
        }
    required_days = {source.period for source in sources}
    selected = select_weekly_items(
        iso_week,
        generation.items,
        required_days=required_days,
    )
    body = render_weekly(iso_week, selected)
    selected_days = {day for item in selected for day in item.source_days}
    missing_days = sorted(required_days - selected_days)
    if not selected or len(body) > WEEKLY_BUDGET or missing_days:
        logger.warning(
            "weekly %s cannot fit complete section/day coverage (missing_days=%s)",
            iso_week,
            missing_days,
        )
        return {
            "weekly_written": False,
            "iso_week": iso_week,
            "generation_status": "failed",
            "missing_source_days": missing_days,
            "bypass": {category: False for category in BYPASS_CATEGORIES},
        }

    generated_at = datetime.now().astimezone().isoformat()
    derivation = _new_derivation(
        "weekly-compress",
        WEEKLY_GENERATION_VERSION,
        provider,
        generated_at=generated_at,
        run_id=run_id,
    )
    MemoryStore(root_path).write_diary(
        {
            "type": "diary",
            "date": iso_week,
            "layer": "week",
            "period": iso_week,
            "promoted_knowledge": [],
            "source_days": [source.period for source in sources],
            "source_hash": current_hash,
            "generated_at": generated_at,
            "generation_status": "ok",
            "generation_version": WEEKLY_GENERATION_VERSION,
            "derivation": derivation,
            "items": [weekly_item_to_dict(item) for item in selected],
            "__body": body,
        }
    )

    bypass_written: dict[str, bool] = {}
    for category in BYPASS_CATEGORIES:
        selected_bypass = select_bypass_items(generation.bypass.get(category, ()))
        path = root_path / "diary" / "bypass" / category / f"{iso_week}.md"
        if not selected_bypass:
            path.unlink(missing_ok=True)
            bypass_written[category] = False
            continue
        _write_bypass_items(
            root_path,
            category,
            iso_week,
            selected_bypass,
            source_days=[source.period for source in sources],
            shash=current_hash,
            generated_at=generated_at,
            derivation=derivation,
        )
        bypass_written[category] = True
    return {
        "weekly_written": True,
        "iso_week": iso_week,
        "generation_status": "ok",
        "source_hash": current_hash,
        "selected_source_days": sorted(selected_days),
        "bypass": bypass_written,
    }


def _write_bypass_items(
    root: Path,
    category: str,
    iso_week: str,
    items: list[WeeklyBypassItem],
    *,
    source_days: list[str],
    shash: str,
    generated_at: str,
    derivation: dict[str, Any],
) -> Path:
    """写入一类 Weekly bypass 的结构化条目、上游日期和生成来源。

    Args:
        root: memory 数据目录。
        category: bypass 类别。
        iso_week: 产物所属的 ISO 周。
        items: 已筛选的结构化 bypass 条目。
        source_days: 本次 Weekly 使用的全部 Daily 日期。
        shash: 全部 Daily 来源的内容哈希。
        generated_at: Weekly 与 bypass 的共同生成时间。
        derivation: Weekly 与 bypass 共用的生成来源记录。

    Returns:
        写入后的 bypass 文件路径。
    """
    path = root / "diary" / "bypass" / category / f"{iso_week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "type": "bypass",
        "category": category,
        "period": iso_week,
        "source_days": source_days,
        "source_hash": shash,
        "generated_at": generated_at,
        "generation_version": WEEKLY_GENERATION_VERSION,
        "derivation": derivation,
        "items": [bypass_item_to_dict(item) for item in items],
    }
    path.write_text(
        _dump_frontmatter(frontmatter, render_bypass(items)), encoding="utf-8"
    )
    return path


def _write_bypass(root: Path, category: str, iso_week: str, body: str) -> Path:
    """把一段正文作为无来源日期的单条 bypass 写入兼容文件。"""
    item = WeeklyBypassItem(body.strip(), ())
    path = root / "diary" / "bypass" / category / f"{iso_week}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _dump_frontmatter(
            {"type": "bypass", "category": category, "period": iso_week},
            render_bypass([item]),
        ),
        encoding="utf-8",
    )
    return path


def _parse_weekly_output(raw: str) -> dict[str, Any]:
    """解析兼容格式的 Weekly JSON；当前生产链不调用此函数。

    找不到或无法解析 JSON 对象时把完整输入视为 Weekly 正文，并返回空 bypass。
    """
    start = raw.find("{")
    if start < 0:
        return {"weekly": raw.strip(), "bypass": {}}
    try:
        data, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError:
        return {"weekly": raw.strip(), "bypass": {}}
    if not isinstance(data, dict):
        return {"weekly": "", "bypass": {}}
    return {
        "weekly": str(data.get("weekly") or "").strip(),
        "bypass": data.get("bypass") if isinstance(data.get("bypass"), dict) else {},
    }


def compress_monthly(
    root: Path | str,
    month: str,
    provider: LLMProvider,
    *,
    force: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """把周一落在目标月份的 Weekly 压缩为 Monthly。

    现有 Monthly 的成功状态、来源哈希、生成版本和正文预算均有效时默认直接复用。
    没有来源或生成失败时不会覆盖已有 Monthly。

    Args:
        root: memory 数据目录。
        month: 目标月份，格式为 ``YYYY-MM``。
        provider: 生成 Monthly 正文的模型客户端。
        force: 是否跳过缓存命中检查并重新生成。
        run_id: 写入来源记录的重生成任务 ID；直接调用时为 None。

    Returns:
        包含 ``monthly_written``、生成状态和月份的报告。``monthly_written`` 在
        新写入成功或缓存命中时为 True；仅新生成成功时包含 ``source_hash``。
    """
    root_path = Path(root)
    sources = monthly_sources(root_path, month)
    if not sources:
        return {
            "monthly_written": False,
            "month": month,
            "generation_status": "missing-source",
        }
    current_hash = source_hash(sources)
    path = _diary_path(root_path, "month", month)
    if not force and _existing_current(
        path,
        expected_hash=current_hash,
        expected_version=_MONTHLY_GENERATION_VERSION,
        budget=_OUTPUT_CAP,
    ):
        return {
            "monthly_written": True,
            "month": month,
            "generation_status": "ok",
            "cached": True,
        }
    body = generate_monthly(provider, sources)
    if body is None:
        return {
            "monthly_written": False,
            "month": month,
            "generation_status": "failed",
        }
    generated_at = datetime.now().astimezone().isoformat()
    MemoryStore(root_path).write_diary(
        {
            "type": "diary",
            "date": month,
            "layer": "month",
            "period": month,
            "promoted_knowledge": [],
            "source_weeks": [source.period for source in sources],
            "source_hash": current_hash,
            "generated_at": generated_at,
            "generation_status": "ok",
            "generation_version": _MONTHLY_GENERATION_VERSION,
            "derivation": _new_derivation(
                "monthly-compress",
                _MONTHLY_GENERATION_VERSION,
                provider,
                generated_at=generated_at,
                run_id=run_id,
            ),
            "__body": body,
        }
    )
    return {
        "monthly_written": True,
        "month": month,
        "generation_status": "ok",
        "source_hash": current_hash,
    }
