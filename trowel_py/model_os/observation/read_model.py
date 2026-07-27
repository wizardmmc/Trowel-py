"""固定 journal 水位上的解释与六维指标聚合。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from trowel_py.model_os.explain import read_decision_explanation
from trowel_py.model_os.journal import (
    JournalBoundary,
    public_journal_label,
    public_journal_ref,
)
from trowel_py.model_os.observation.models import (
    CostSummary,
    EvidenceFact,
    MetricDimension,
    MetricRatio,
    MetricsReport,
    ScopeExplanation,
)
from trowel_py.model_os.types import DecisionRecord, EventEnvelope
from trowel_py.model_os.redaction import redact_payload

_MAX_SCOPE_ITEMS = 200
_REF_KEYS = frozenset(
    {
        "candidate_id",
        "candidate_ids",
        "evidence_ref",
        "evidence_refs",
        "generation_id",
        "observation_id",
        "plan_id",
        "request_event_id",
        "schedule_decision_id",
    }
)


def _domain(kind: str, payload: dict[str, Any]) -> str:
    command_kind = payload.get("command_kind")
    if isinstance(command_kind, str):
        if command_kind.startswith("default."):
            return "default"
        if command_kind.startswith("incubation."):
            return "incubation"
    if kind == "wake.consumed":
        return "wake"
    if kind.startswith("attention."):
        return "switch"
    if kind.startswith(("route.", "cognitive.route")):
        return "route"
    if kind.startswith("work_broker."):
        return "budget"
    return "continuity"


def _source_class(event: EventEnvelope) -> str:
    if event.kind == "cognitive_signal.recorded":
        signal = event.payload.get("signal")
        if isinstance(signal, dict):
            kind = signal.get("kind")
            identifier = kind.get("identifier") if isinstance(kind, dict) else kind
            if isinstance(identifier, str):
                if "user_feedback" in identifier:
                    return "user_feedback"
                if "validator_outcome" in identifier:
                    return "verifier"
                if "model_report" in identifier:
                    return "model_report"
    if event.provenance.value == "user_decision":
        return "user_feedback"
    if event.provenance.value == "model_hypothesis":
        return "model_report"
    if event.provenance.value == "machine_observation":
        return "machine_observation"
    return "unknown"


def _safe_refs(payload: dict[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in sorted(_REF_KEYS):
        value = payload.get(key)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item:
                refs.append(public_journal_ref(item) or "unknown")
    return tuple(dict.fromkeys(refs))


def _safe_token(value: Any) -> str | None:
    if (
        isinstance(value, str)
        and 0 < len(value) <= 128
        and all(char.isalnum() or char in "_.:/-" for char in value)
    ) and redact_payload(value) == value:
        return value
    return None


def read_scope_explanation(
    conn: sqlite3.Connection,
    *,
    subject_kind: str,
    subject_id: str,
    boundary: JournalBoundary,
    decode_decision: Callable[[sqlite3.Row], DecisionRecord],
    decode_event: Callable[[sqlite3.Row], EventEnvelope],
) -> ScopeExplanation:
    column = "task_id" if subject_kind == "task" else "episode_id"
    decision_params: list[Any] = [boundary.decision_seq, subject_id]
    event_params: list[Any] = [boundary.event_seq, subject_id]
    decision_link_sql = ""
    event_link_sql = ""
    links_truncated = False
    if subject_kind == "task":
        work_rows = conn.execute(
            "SELECT work_item_id FROM events WHERE seq<=? AND task_id=? "
            "AND work_item_id IS NOT NULL "
            "UNION SELECT work_item_id FROM decisions WHERE seq<=? "
            "AND task_id=? AND work_item_id IS NOT NULL "
            "ORDER BY work_item_id LIMIT 65",
            (
                boundary.event_seq,
                subject_id,
                boundary.decision_seq,
                subject_id,
            ),
        ).fetchall()
        links_truncated = len(work_rows) > 64
        work_ids = tuple(row["work_item_id"] for row in work_rows[:64])
        if work_ids:
            placeholders = ",".join("?" for _ in work_ids)
            decision_link_sql = f" OR work_item_id IN ({placeholders})"
            event_link_sql = decision_link_sql
            decision_params.extend(work_ids)
            event_params.extend(work_ids)
    else:
        link_rows = conn.execute(
            "SELECT cause_id, correlation_id FROM events WHERE seq<=? "
            "AND episode_id=? AND (cause_id IS NOT NULL OR correlation_id IS NOT NULL) "
            "ORDER BY seq LIMIT 65",
            (boundary.event_seq, subject_id),
        ).fetchall()
        links_truncated = len(link_rows) > 64
        link_rows = link_rows[:64]
        cause_ids = tuple(
            dict.fromkeys(
                row["cause_id"] for row in link_rows if row["cause_id"] is not None
            )
        )
        correlations = tuple(
            dict.fromkeys(
                row["correlation_id"]
                for row in link_rows
                if row["correlation_id"] is not None
            )
        )
        if cause_ids:
            placeholders = ",".join("?" for _ in cause_ids)
            decision_link_sql += f" OR decision_id IN ({placeholders})"
            decision_params.extend(cause_ids)
        if correlations:
            placeholders = ",".join("?" for _ in correlations)
            decision_link_sql += f" OR correlation_id IN ({placeholders})"
            event_link_sql += f" OR correlation_id IN ({placeholders})"
            decision_params.extend(correlations)
            event_params.extend(correlations)
    decision_params.append(_MAX_SCOPE_ITEMS + 1)
    event_params.append(_MAX_SCOPE_ITEMS + 1)
    decision_rows = conn.execute(
        f"SELECT * FROM decisions WHERE seq<=? AND ({column}=?{decision_link_sql}) "
        "ORDER BY seq LIMIT ?",
        decision_params,
    ).fetchall()
    event_rows = conn.execute(
        f"SELECT * FROM events WHERE seq<=? AND ({column}=?{event_link_sql}) "
        "ORDER BY seq LIMIT ?",
        event_params,
    ).fetchall()
    truncated = (
        links_truncated
        or len(decision_rows) > _MAX_SCOPE_ITEMS
        or len(event_rows) > _MAX_SCOPE_ITEMS
    )
    decision_rows = decision_rows[:_MAX_SCOPE_ITEMS]
    event_rows = event_rows[:_MAX_SCOPE_ITEMS]
    explanations = tuple(
        read_decision_explanation(
            conn,
            decision_id=row["decision_id"],
            boundary=boundary,
            decode_decision=decode_decision,
            decode_event=decode_event,
        )
        for row in decision_rows
    )
    facts: list[EvidenceFact] = []
    source_counts = {
        "machine_observation": 0,
        "user_feedback": 0,
        "verifier": 0,
        "model_report": 0,
        "unknown": 0,
    }
    for row in event_rows:
        event = decode_event(row)
        source_class = _source_class(event)
        source_counts[source_class] += 1
        payload = event.payload
        command_kind = payload.get("command_kind")
        fact_kind = command_kind if isinstance(command_kind, str) else str(event.kind)
        facts.append(
            EvidenceFact(
                event_id=public_journal_ref(event.event_id) or "unknown",
                domain=_domain(str(event.kind), payload),
                kind=public_journal_label(fact_kind),
                source_class=source_class,
                provenance=event.provenance.value,
                policy_version=public_journal_label(event.policy_version),
                model_version=_safe_token(payload.get("model")),
                work_item_id=public_journal_ref(event.work_item_id),
                task_id=public_journal_ref(event.task_id),
                episode_id=public_journal_ref(event.episode_id),
                cause_id=public_journal_ref(event.cause_id),
                correlation_id=public_journal_ref(event.correlation_id),
                outcome=(
                    public_journal_label(event.outcome)
                    if event.outcome is not None
                    else None
                )
                or _safe_token(payload.get("result_code"))
                or _safe_token(payload.get("unknown_code"))
                or _safe_token(payload.get("outcome"))
                or _safe_token(payload.get("disposition")),
                evidence_refs=_safe_refs(payload),
            )
        )
    return ScopeExplanation(
        subject_kind=subject_kind,
        subject_id=public_journal_ref(subject_id) or "unknown",
        as_of=boundary,
        decisions=explanations,
        facts=tuple(facts),
        source_counts=source_counts,
        truncated=truncated,
    )


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _versions(
    conn: sqlite3.Connection,
    *,
    boundary: JournalBoundary,
    start: str,
    end: str,
    event_kinds: tuple[str, ...] = (),
    decision_kinds: tuple[str, ...] = (),
) -> tuple[str, ...]:
    values: set[str] = set()
    if event_kinds:
        placeholders = ",".join("?" for _ in event_kinds)
        rows = conn.execute(
            f"SELECT DISTINCT policy_version FROM events WHERE seq<=? "
            f"AND occurred_at>=? AND occurred_at<? AND kind IN ({placeholders})",
            (boundary.event_seq, start, end, *event_kinds),
        ).fetchall()
        values.update(public_journal_label(row[0]) for row in rows)
    if decision_kinds:
        placeholders = ",".join("?" for _ in decision_kinds)
        rows = conn.execute(
            f"SELECT DISTINCT policy_version FROM decisions WHERE seq<=? "
            f"AND decided_at>=? AND decided_at<? AND kind IN ({placeholders})",
            (boundary.decision_seq, start, end, *decision_kinds),
        ).fetchall()
        values.update(public_journal_label(row[0]) for row in rows)
    return tuple(sorted(values))


def _ratio(name: str, numerator: int, denominator: int) -> MetricRatio:
    return MetricRatio(
        name=name,
        numerator=numerator,
        denominator=denominator,
        unknown=max(0, denominator - numerator),
    )


def _known_ratio(
    name: str,
    *,
    numerator: int,
    denominator: int,
    unknown: int,
) -> MetricRatio:
    return MetricRatio(
        name=name,
        numerator=numerator,
        denominator=denominator,
        unknown=unknown,
    )


def _command_versions(
    conn: sqlite3.Connection,
    *,
    boundary: JournalBoundary,
    start: str,
    end: str,
    domain: str,
) -> tuple[str, ...]:
    rows = conn.execute(
        "SELECT DISTINCT policy_version FROM events WHERE seq<=? "
        "AND occurred_at>=? AND occurred_at<? "
        "AND json_extract(payload,'$.command_kind') LIKE ?",
        (boundary.event_seq, start, end, f"{domain}.%"),
    ).fetchall()
    return tuple(sorted({public_journal_label(row[0]) for row in rows}))


def _empty_cost() -> CostSummary:
    return CostSummary(None, 0, 0, ())


def _generation_summary(
    conn: sqlite3.Connection,
    *,
    boundary: JournalBoundary,
    start: str,
    end: str,
    command_kind: str,
    payload_id: str,
    table: str,
    table_id: str,
) -> tuple[tuple[str, ...], CostSummary]:
    rows = conn.execute(
        f"SELECT generation.effective_model AS model, generation.cost AS cost "
        f"FROM events result JOIN {table} generation "
        f"ON generation.{table_id}=json_extract(result.payload, ?) "
        "WHERE result.seq<=? AND result.occurred_at>=? AND result.occurred_at<? "
        "AND result.kind='command.result' "
        "AND json_extract(result.payload,'$.command_kind')=?",
        (
            f"$.{payload_id}",
            boundary.event_seq,
            start,
            end,
            command_kind,
        ),
    ).fetchall()
    known_costs = [float(row["cost"]) for row in rows if row["cost"] is not None]
    models = tuple(
        sorted(
            {
                safe
                for row in rows
                if (safe := _safe_token(row["model"])) is not None
            }
        )
    )
    return (
        models,
        CostSummary(
            known_total=sum(known_costs) if known_costs else None,
            known_count=len(known_costs),
            unknown_count=sum(row["cost"] is None for row in rows),
            sources=("generation_usage",) if rows else (),
        ),
    )


def read_metrics(
    conn: sqlite3.Connection,
    *,
    boundary: JournalBoundary,
    window_start: str,
    window_end: str,
) -> MetricsReport:
    ev = (boundary.event_seq, window_start, window_end)
    dec = (boundary.decision_seq, window_start, window_end)

    continuity_known = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind IN "
        "('episode.closed','episode.failed','episode.cancelled')",
        ev,
    )
    continuity_unknown = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind IN "
        "('episode.reconcile_required','episode.interrupt_reconcile_required')",
        ev,
    )
    recovery_total = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='attention.switch_recovery_started'",
        ev,
    )
    recovery_known = _count(
        conn,
        "SELECT COUNT(*) FROM events started WHERE started.seq<=? "
        "AND started.occurred_at>=? AND started.occurred_at<? "
        "AND started.kind='attention.switch_recovery_started' AND EXISTS "
        "(SELECT 1 FROM events observed WHERE observed.seq<=? "
        "AND observed.kind='attention.switch_recovery_observed' "
        "AND observed.cause_id=started.cause_id)",
        (*ev, boundary.event_seq),
    )
    repeated_total = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='attention.switch_recovery_observed'",
        ev,
    )
    repeated_unknown = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='attention.switch_recovery_observed' "
        "AND (json_extract(payload,'$.unavailable_reason') IS NOT NULL "
        "OR json_type(payload,'$.repeated_tool_candidates') IS NULL)",
        ev,
    )
    repeated_clean = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='attention.switch_recovery_observed' "
        "AND json_extract(payload,'$.unavailable_reason') IS NULL "
        "AND json_array_length(json_extract(payload,'$.repeated_tool_candidates'))=0",
        ev,
    )

    scheduling_total = _count(
        conn,
        "SELECT COUNT(*) FROM decisions WHERE seq<=? AND decided_at>=? "
        "AND decided_at<? AND kind='attention.schedule'",
        dec,
    )
    scheduling_known = _count(
        conn,
        "SELECT COUNT(*) FROM decisions d WHERE d.seq<=? AND d.decided_at>=? "
        "AND d.decided_at<? AND d.kind='attention.schedule' AND "
        "(d.disposition='no_action' OR EXISTS "
        "(SELECT 1 FROM events e WHERE e.seq<=? AND e.cause_id=d.decision_id "
        "AND e.kind IN ('command.result','attention.resource_deferred')))",
        (*dec, boundary.event_seq),
    )
    wake_total = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='wake.consumed'",
        ev,
    )
    wake_scheduled = _count(
        conn,
        "SELECT COUNT(*) FROM events wake WHERE wake.seq<=? "
        "AND wake.occurred_at>=? AND wake.occurred_at<? "
        "AND wake.kind='wake.consumed' AND EXISTS "
        "(SELECT 1 FROM decisions d, json_each(d.signals,'$.refs') ref "
        "WHERE d.seq<=? AND d.kind='attention.schedule' "
        "AND ref.value=wake.event_id)",
        (*ev, boundary.decision_seq),
    )

    route_total = _count(
        conn,
        "SELECT COUNT(*) FROM decisions WHERE seq<=? AND decided_at>=? "
        "AND decided_at<? AND kind='cognitive.route'",
        dec,
    )
    route_known = _count(
        conn,
        "SELECT COUNT(*) FROM decisions d WHERE d.seq<=? AND d.decided_at>=? "
        "AND d.decided_at<? AND d.kind='cognitive.route' AND EXISTS "
        "(SELECT 1 FROM events e WHERE e.seq<=? AND e.cause_id=d.decision_id "
        "AND e.kind='route.actual_observed')",
        (*dec, boundary.event_seq),
    )
    override_total = _count(
        conn,
        "SELECT COUNT(*) FROM decisions WHERE seq<=? AND decided_at>=? "
        "AND decided_at<? AND kind='cognitive.route' "
        "AND json_extract(candidates,'$[0].preference') IN ('fast','deep')",
        dec,
    )
    override_known = _count(
        conn,
        "SELECT COUNT(*) FROM decisions d WHERE d.seq<=? AND d.decided_at>=? "
        "AND d.decided_at<? AND d.kind='cognitive.route' "
        "AND json_extract(d.candidates,'$[0].preference') IN ('fast','deep') "
        "AND EXISTS (SELECT 1 FROM events e WHERE e.seq<=? "
        "AND e.cause_id=d.decision_id AND e.kind='route.actual_observed' "
        "AND json_extract(e.payload,'$.tier')=d.choice)",
        (*dec, boundary.event_seq),
    )
    override_observed = _count(
        conn,
        "SELECT COUNT(*) FROM decisions d WHERE d.seq<=? AND d.decided_at>=? "
        "AND d.decided_at<? AND d.kind='cognitive.route' "
        "AND json_extract(d.candidates,'$[0].preference') IN ('fast','deep') "
        "AND EXISTS (SELECT 1 FROM events e WHERE e.seq<=? "
        "AND e.cause_id=d.decision_id AND e.kind='route.actual_observed')",
        (*dec, boundary.event_seq),
    )
    review_total = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='route.review_recorded'",
        ev,
    )
    review_trusted = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='route.review_recorded' "
        "AND json_extract(payload,'$.trusted_verifier')=1 "
        "AND json_extract(payload,'$.classification')!='unknown'",
        ev,
    )
    review_unknown = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? "
        "AND occurred_at<? AND kind='route.review_recorded' "
        "AND json_extract(payload,'$.classification')='unknown'",
        ev,
    )

    default_total = _count(
        conn,
        "SELECT COALESCE(SUM(CAST(json_extract(payload,'$.candidate_count') AS INT)),0) "
        "FROM events WHERE seq<=? AND occurred_at>=? AND occurred_at<? "
        "AND kind='command.result' "
        "AND json_extract(payload,'$.command_kind')='default.run_pilot'",
        ev,
    )
    default_known = _count(
        conn,
        "WITH generated(candidate_id) AS ("
        "SELECT value FROM events, json_each(events.payload,'$.candidate_ids') "
        "WHERE events.seq<=? AND events.occurred_at>=? AND events.occurred_at<? "
        "AND events.kind='command.result' "
        "AND json_extract(events.payload,'$.command_kind')='default.run_pilot') "
        "SELECT COUNT(DISTINCT generated.candidate_id) FROM generated "
        "JOIN events outcome ON outcome.seq<=? AND outcome.occurred_at<? "
        "AND outcome.kind='command.result' "
        "AND json_extract(outcome.payload,'$.command_kind')="
        "'default.candidate_outcome' "
        "AND json_extract(outcome.payload,'$.candidate_id')=generated.candidate_id",
        (*ev, boundary.event_seq, window_end),
    )

    incubation_total = _count(
        conn,
        "SELECT COALESCE(SUM(json_array_length(json_extract(payload,'$.candidate_ids'))),0) "
        "FROM events WHERE seq<=? AND occurred_at>=? AND occurred_at<? "
        "AND kind='command.result' "
        "AND json_extract(payload,'$.command_kind')='incubation.run_cycle'",
        ev,
    )
    incubation_known = _count(
        conn,
        "WITH generated(candidate_id) AS ("
        "SELECT value FROM events, json_each(events.payload,'$.candidate_ids') "
        "WHERE events.seq<=? AND events.occurred_at>=? AND events.occurred_at<? "
        "AND events.kind='command.result' "
        "AND json_extract(events.payload,'$.command_kind')='incubation.run_cycle') "
        "SELECT COUNT(DISTINCT generated.candidate_id) FROM generated "
        "JOIN events outcome ON outcome.seq<=? AND outcome.occurred_at<? "
        "AND outcome.kind='command.result' "
        "AND json_extract(outcome.payload,'$.command_kind')="
        "'incubation.candidate_outcome' "
        "AND json_extract(outcome.payload,'$.candidate_id')=generated.candidate_id",
        (*ev, boundary.event_seq, window_end),
    )

    command_total = _count(
        conn,
        "SELECT COUNT(*) FROM events WHERE seq<=? AND occurred_at>=? AND occurred_at<? "
        "AND kind='command.intent'",
        ev,
    )
    command_known = _count(
        conn,
        "SELECT COUNT(*) FROM events intent WHERE intent.seq<=? "
        "AND intent.occurred_at>=? AND intent.occurred_at<? "
        "AND intent.kind='command.intent' AND EXISTS "
        "(SELECT 1 FROM events terminal WHERE terminal.seq<=? "
        "AND terminal.correlation_id=intent.correlation_id "
        "AND terminal.kind='command.result')",
        (*ev, boundary.event_seq),
    )
    broker_total = _count(
        conn,
        "SELECT COUNT(*) FROM decisions WHERE seq<=? AND decided_at>=? "
        "AND decided_at<? AND kind='work_broker.arbitrate'",
        dec,
    )

    usage_rows = conn.execute(
        "SELECT json_extract(candidates,'$[0].cost') AS cost, "
        "json_extract(candidates,'$[0].cost_source') AS source "
        "FROM decisions WHERE seq<=? AND decided_at>=? AND decided_at<? "
        "AND kind='work_broker.usage'",
        dec,
    ).fetchall()
    known_costs = [float(row["cost"]) for row in usage_rows if row["cost"] is not None]
    cost = CostSummary(
        known_total=sum(known_costs) if known_costs else None,
        known_count=len(known_costs),
        unknown_count=sum(row["cost"] is None for row in usage_rows),
        sources=tuple(
            sorted({public_journal_label(row["source"]) for row in usage_rows})
        ),
    )
    default_models, default_cost = _generation_summary(
        conn,
        boundary=boundary,
        start=window_start,
        end=window_end,
        command_kind="default.run_pilot",
        payload_id="generation_id",
        table="default_generations",
        table_id="generation_id",
    )
    incubation_models, incubation_cost = _generation_summary(
        conn,
        boundary=boundary,
        start=window_start,
        end=window_end,
        command_kind="incubation.run_cycle",
        payload_id="plan_id",
        table="incubation_plans",
        table_id="plan_id",
    )

    continuity_kinds = (
        "episode.closed",
        "episode.failed",
        "episode.cancelled",
        "episode.reconcile_required",
        "episode.interrupt_reconcile_required",
    )
    dimensions = (
        MetricDimension(
            "continuity",
            (
                _ratio(
                    "terminal_outcome_known",
                    continuity_known,
                    continuity_known + continuity_unknown,
                ),
                _ratio("switch_recovery_observed", recovery_known, recovery_total),
                _known_ratio(
                    "recovery_without_repeated_tool",
                    numerator=repeated_clean,
                    denominator=repeated_total,
                    unknown=repeated_unknown,
                ),
            ),
            _versions(
                conn,
                boundary=boundary,
                start=window_start,
                end=window_end,
                event_kinds=continuity_kinds,
            ),
            (),
            _empty_cost(),
        ),
        MetricDimension(
            "scheduling",
            (
                _ratio("attention_resolved", scheduling_known, scheduling_total),
                _ratio("wake_scheduled", wake_scheduled, wake_total),
            ),
            _versions(
                conn,
                boundary=boundary,
                start=window_start,
                end=window_end,
                decision_kinds=("attention.schedule",),
            ),
            (),
            _empty_cost(),
        ),
        MetricDimension(
            "routing",
            (
                _ratio("actual_route_observed", route_known, route_total),
                _known_ratio(
                    "user_override_matched",
                    numerator=override_known,
                    denominator=override_total,
                    unknown=max(0, override_total - override_observed),
                ),
                _known_ratio(
                    "trusted_verifier_review",
                    numerator=review_trusted,
                    denominator=review_total,
                    unknown=review_unknown,
                ),
            ),
            _versions(
                conn,
                boundary=boundary,
                start=window_start,
                end=window_end,
                decision_kinds=("cognitive.route",),
            ),
            tuple(
                sorted(
                    {
                        safe
                        for row in conn.execute(
                            "SELECT DISTINCT json_extract(payload,'$.model') "
                            "FROM events WHERE seq<=? AND occurred_at>=? "
                            "AND occurred_at<? AND kind='route.actual_observed' "
                            "AND json_extract(payload,'$.model') IS NOT NULL",
                            ev,
                        ).fetchall()
                        if (safe := _safe_token(row[0])) is not None
                    }
                )
            ),
            _empty_cost(),
        ),
        MetricDimension(
            "default",
            (_ratio("candidate_outcome_recorded", default_known, default_total),),
            _command_versions(
                conn,
                boundary=boundary,
                start=window_start,
                end=window_end,
                domain="default",
            ),
            default_models,
            default_cost,
        ),
        MetricDimension(
            "incubation",
            (_ratio("candidate_outcome_recorded", incubation_known, incubation_total),),
            _command_versions(
                conn,
                boundary=boundary,
                start=window_start,
                end=window_end,
                domain="incubation",
            ),
            incubation_models,
            incubation_cost,
        ),
        MetricDimension(
            "reliability",
            (
                _ratio(
                    "command_or_arbitration_known",
                    command_known + broker_total,
                    command_total + broker_total,
                ),
            ),
            _versions(
                conn,
                boundary=boundary,
                start=window_start,
                end=window_end,
                event_kinds=("command.intent", "command.result", "command.unknown"),
                decision_kinds=("work_broker.arbitrate", "work_broker.usage"),
            ),
            (),
            cost,
        ),
    )
    return MetricsReport(window_start, window_end, boundary, dimensions)
