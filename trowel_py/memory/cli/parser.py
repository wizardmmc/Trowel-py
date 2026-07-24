"""Memory CLI 参数模型。"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """构造 memory 子命令解析器。"""
    parser = argparse.ArgumentParser(
        prog="trowel-py memory",
        description="memory subsystem",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    tidy = sub.add_parser("tidy", help="weekly/monthly tidy + rollback (041)")
    tidy.add_argument(
        "--weekly",
        action="store_true",
        help="run weekly tidy (compress + bypass + TidyPlan) for one ISO week",
    )
    tidy.add_argument(
        "--monthly",
        action="store_true",
        help="run monthly tidy (compress + retire + promote + TidyPlan)",
    )
    tidy.add_argument("--iso-week", help="ISO week YYYY-Www (default: current week)")
    tidy.add_argument("--month", help="YYYY-MM (default: current month)")
    tidy.add_argument("--rollback", help="rollback a tidy plan id (restore notes/)")
    tidy.add_argument(
        "--status",
        action="store_true",
        help="print the tidy watermark + pending periods (read-only, 063)",
    )
    tidy.add_argument(
        "--catchup",
        action="store_true",
        help="explicitly catch up a range; needs --from and --scope (063)",
    )
    tidy.add_argument(
        "--from",
        dest="from_period",
        metavar="PERIOD",
        help="starting period for --catchup (YYYY-Www weekly, YYYY-MM monthly)",
    )
    tidy.add_argument(
        "--scope",
        choices=["weekly", "monthly"],
        help="which scope --catchup runs",
    )
    tidy.add_argument("--root", help="memory root (default: resolved from config.toml)")

    review = sub.add_parser(
        "review",
        help="run the daily write loop: distill a day's cc sessions",
    )
    review.add_argument("--date", help="target day YYYY-MM-DD (default: today)")
    review.add_argument(
        "--root", help="memory root (default: resolved from config.toml)"
    )

    repair = sub.add_parser(
        "repair",
        help="backfill per-session episodes from surviving drafts (P1 overwrite fix)",
    )
    repair.add_argument("--date", required=True, help="target day YYYY-MM-DD")
    repair.add_argument(
        "--apply",
        action="store_true",
        help="apply (back up + write); default is a dry-run listing",
    )
    repair.add_argument(
        "--root", help="memory root (default: resolved from config.toml)"
    )

    backfill = sub.add_parser(
        "backfill-completed",
        help="stamp last_completed_offset for legacy rows from jsonl size (040-b)",
    )
    backfill.add_argument("--date", required=True, help="target day YYYY-MM-DD")
    backfill.add_argument(
        "--apply",
        action="store_true",
        help="apply (write); default is a dry-run listing",
    )
    backfill.add_argument(
        "--root",
        help="memory root (default: resolved from config.toml)",
    )

    dict_rebuild = sub.add_parser(
        "dict-rebuild",
        help="regenerate dictionary L0/L1 from notes (040-c C-3)",
    )
    dict_rebuild.add_argument(
        "--apply",
        action="store_true",
        help="write; default is dry-run",
    )
    dict_rebuild.add_argument(
        "--root",
        help="memory root (default: resolved from config.toml)",
    )

    dict_check = sub.add_parser(
        "dict-check",
        help="read-only dictionary consistency report vs notes (064)",
    )
    dict_check.add_argument(
        "--root",
        help="memory root (default: resolved from config.toml)",
    )

    migrate = sub.add_parser(
        "migrate",
        help="backfill memory_id/status/valid_from on legacy notes (041 C-9)",
    )
    migrate.add_argument(
        "--apply",
        action="store_true",
        help="write; default is dry-run",
    )
    migrate.add_argument(
        "--root", help="memory root (default: resolved from config.toml)"
    )

    core = sub.add_parser(
        "core",
        help="layer-one promotion: nominate/approve/activate (041)",
    )
    core_sub = core.add_subparsers(dest="core_cmd", required=True)
    core_nominate = core_sub.add_parser(
        "nominate",
        help="nominate a note as core candidate",
    )
    core_nominate.add_argument("note_stem", help="note filename stem")
    core_approve = core_sub.add_parser(
        "approve",
        help="candidate → core.md (status=trial)",
    )
    core_approve.add_argument("candidate_id", help="candidate memory_id")
    core_activate = core_sub.add_parser("activate", help="core item trial → active")
    core_activate.add_argument("memory_id", help="core item memory_id")
    core.add_argument("--root", help="memory root (default: resolved from config.toml)")

    metrics = sub.add_parser(
        "metrics",
        help="print memory metrics (065): identity/retrieval/effect/recall "
        "with coverage + quality labels + the policy in force",
    )
    metrics.add_argument(
        "--root", help="memory root (default: resolved from config.toml)"
    )

    promotion = sub.add_parser(
        "promotion",
        help="evaluate core-candidate promotion gaps (065); --apply writes candidates",
    )
    promotion.add_argument(
        "--policy",
        help="path to a PromotionPolicy JSON override",
    )
    promotion.add_argument(
        "--apply",
        action="store_true",
        help="write/refresh candidate files (default: dry-run gap report)",
    )
    promotion.add_argument(
        "--root",
        help="memory root (default: resolved from config.toml)",
    )

    recalibrate = sub.add_parser(
        "profile-recalibrate",
        help="re-distill history under v2 hard rules into isolated staging "
        "(067); default is a read-only plan, never touching live data",
    )
    recalibrate.add_argument(
        "--all",
        action="store_true",
        help="replay every user completed session (exclusive with --from)",
    )
    recalibrate.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        help="replay sessions on/after this date (exclusive with --all)",
    )
    recalibrate.add_argument(
        "--run",
        action="store_true",
        help="run the shadow replay (default: read-only plan)",
    )
    recalibrate.add_argument(
        "--proxy-base-url",
        help="trowel proxy URL (required for --run; never bypass slice-030)",
    )
    recalibrate.add_argument(
        "--root",
        help="memory root (default: resolved from config.toml)",
    )
    return parser
