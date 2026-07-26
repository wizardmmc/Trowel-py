"""从 Route journal 计算上线门禁。"""

from __future__ import annotations

from trowel_py.model_os.routing.journal import (
    ROUTE_ACTUAL_KIND,
    ROUTE_APPROVAL_KIND,
    ROUTE_DECISION_KIND,
    ROUTE_REVIEW_KIND,
)
from trowel_py.model_os.routing.models import RouteGateSnapshot, RouteReviewClass


def _candidate(row, role: str):
    return next(
        (
            item
            for item in row.candidates
            if isinstance(item, dict) and item.get("role") == role
        ),
        None,
    )


def build_route_gate(store) -> RouteGateSnapshot:
    with store._read_tx():
        decisions = tuple(
            item
            for _, item in store.list_decisions()
            if item.kind == ROUTE_DECISION_KIND
        )
        events = tuple(item for _, item in store.list_events())
        boundary = store.journal_boundary()

    actuals = {item.cause_id: item for item in events if item.kind == ROUTE_ACTUAL_KIND}
    reviews = {item.cause_id: item for item in events if item.kind == ROUTE_REVIEW_KIND}
    terminal_episode_ids = {
        item.episode_id
        for item in events
        if item.kind == "command.result"
        and item.episode_id is not None
        and item.payload.get("result_code") == "terminal_observed"
    }
    completed = [
        item
        for item in decisions
        if item.decision_id in actuals
        and actuals[item.decision_id].episode_id in terminal_episode_ids
        and _candidate(item, "input") is not None
        and _candidate(item, "input").get("mode") == "shadow"
    ]
    domains = tuple(
        sorted(
            {
                str(_candidate(item, "input").get("evaluation_domain"))
                for item in completed
                if _candidate(item, "input") is not None
                and _candidate(item, "input").get("evaluation_domain")
                not in {None, "unknown"}
            }
        )
    )
    trusted = sum(
        bool(reviews[item.decision_id].payload.get("trusted_verifier"))
        for item in completed
        if item.decision_id in reviews
    )
    classifications = [
        str(reviews[item.decision_id].payload.get("classification", "unknown"))
        for item in completed
        if item.decision_id in reviews
    ]
    missed = classifications.count(RouteReviewClass.MISSED_DEEP_NEED.value)
    unjustified = classifications.count(RouteReviewClass.UNJUSTIFIED_DEEP.value)
    verifier_versions = tuple(
        sorted(
            {
                str(version)
                for review in reviews.values()
                for version in review.payload.get("verifier_versions", ())
            }
        )
    )
    explicit = [
        item
        for item in completed
        if _candidate(item, "input") is not None
        and _candidate(item, "input").get("preference") in {"fast", "deep"}
    ]
    executed = 0
    for item in explicit:
        proposed = _candidate(item, "proposed")
        actual = actuals[item.decision_id].payload
        if proposed is not None and (
            proposed.get("model"),
            proposed.get("effort"),
        ) == (actual.get("model"), actual.get("effort")):
            executed += 1
    actual_match = 0
    for item in completed:
        expected = _candidate(item, "actual")
        observed = actuals[item.decision_id].payload
        if expected is not None and (
            expected.get("tier"),
            expected.get("model"),
            expected.get("effort"),
        ) == (
            observed.get("tier"),
            observed.get("model"),
            observed.get("effort"),
        ):
            actual_match += 1
    ready = (
        len(completed) >= 30
        and len(domains) >= 3
        and trusted >= 10
        and missed <= 2
        and unjustified <= 3
        and executed == len(explicit)
        and actual_match == len(completed)
    )
    return RouteGateSnapshot(
        live_episodes=len(completed),
        reviewed_episodes=len(classifications),
        domains=domains,
        trusted_verifier_episodes=trusted,
        trusted_verifier_versions=verifier_versions,
        missed_deep_need=missed,
        unjustified_deep=unjustified,
        review_unknown=len(completed)
        - len(classifications)
        + classifications.count(RouteReviewClass.UNKNOWN.value),
        user_override_total=len(explicit),
        user_override_executed=executed,
        actual_match_total=len(completed),
        actual_match_executed=actual_match,
        ready_for_human_review=ready,
        canary_approved=ready
        and any(
            item.kind == ROUTE_APPROVAL_KIND
            and item.policy_version == "m8-l10-paired-20260723"
            for item in events
        ),
        as_of=boundary,
    )
