"""Core 冷启动种子与幂等初始化。"""

from __future__ import annotations

from pathlib import Path

import yaml

from trowel_py.memory.types import CoreItem

CORE_SEED_ITEMS: tuple[CoreItem, ...] = (
    CoreItem(
        id="lookup-first",
        imperative=(
            "遇问题先查 memory（dictionary + notes）→ web search → 本地尝试 → "
            "万策尽才问人。永不假设（对高风险决策）。"
        ),
        scope="high-risk",
        status="seed",
        source="milestone6-v2 §③ + trowel CLAUDE.md",
    ),
    CoreItem(
        id="triage",
        imperative=(
            "低风险决策允许快速假设并明确标注；高风险走全套查证。别对改变量名也铺满"
            "检索（防 ownership 表演）。"
        ),
        scope="high-risk",
        status="seed",
        source="milestone6-v2 §🅓",
    ),
    CoreItem(
        id="write-verification",
        imperative=(
            "把结论写进 notes 前，问「根因假设是否实测过」。turn 耗时 / 测试通过 / "
            "commit 已落都不替代根因假设的 spike 实测。方案若建立在从未独立观测的根因"
            "断言上，标 verification: inferred-untested。"
        ),
        scope="high-risk",
        status="seed",
        source="milestone6-v2-spike-report S4",
    ),
    CoreItem(
        id="retire-not-forget",
        imperative=(
            "低 refs / 半衰期到的条目降级退场（不进默认注入），但文件留、dictionary 留"
            "索引，需要时能 read 找回。"
        ),
        scope="high-risk",
        status="seed",
        source="milestone6-v2 §🅒",
    ),
    CoreItem(
        id="dual-track",
        imperative=(
            "知识轨记可复用结论；经历轨记事件流（时间 / 做了啥 / 卡哪 / 痛感）。周整理"
            "把日记里的知识提拔进 notes。"
        ),
        scope="high-risk",
        status="seed",
        source="milestone6-v2 §🅑",
    ),
    CoreItem(
        id="spike-first",
        imperative="设计前 spike 实测假设，不靠已有资料的结论（对高风险决策）。",
        scope="high-risk",
        status="seed",
        source="trowel CLAUDE.md「做事」",
    ),
    CoreItem(
        id="second-feedback-reverse",
        imperative=(
            "同一个 bug 第二次被反馈，必须停下重新逆向 / 读源代码，不许第三次试错。"
        ),
        scope="high-risk",
        status="seed",
        source="trowel CLAUDE.md「做事」",
    ),
    CoreItem(
        id="real-data-test",
        imperative="测试数据用真实数据，除非真找不到才允许合成数据测试。",
        scope="high-risk",
        status="seed",
        source="trowel CLAUDE.md「开发规范」",
    ),
)

SEED_KEYWORDS: tuple[str, ...] = (
    "查 memory",
    "低风险",
    "根因假设",
    "退场",
    "知识轨",
    "spike",
    "第二次",
    "真实数据",
)


def bootstrap_core(root: Path | str, *, force: bool = False) -> bool:
    """初始化 ``core.md``；默认不覆盖已有的人工维护内容。"""
    path = Path(root) / "core.md"
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_core_md(CORE_SEED_ITEMS), encoding="utf-8")
    return True


def _render_core_md(items: tuple[CoreItem, ...]) -> str:
    fm = {
        "type": "core",
        "items": [
            {
                "id": it.id,
                "imperative": it.imperative,
                "scope": it.scope,
                "status": it.status,
                "source": it.source,
            }
            for it in items
        ],
    }
    body_lines = [
        "# 层一（core）— 试用期种子\n",
        "\n",
        "> 全部 status: seed，正式写入需人工 review（041）。\n\n",
    ]
    for i, it in enumerate(items, 1):
        body_lines.append(f"{i}. {it.imperative}\n")
    dumped = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    return f"---\n{dumped}---\n{''.join(body_lines)}"
