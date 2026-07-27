"""Candidate、generation 与 outcome 的事务式读写。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)

from .codec import normalized_claim_hash
from .models import (
    POLICY_VERSION,
    BeginGeneration,
    Candidate,
    CandidateDraft,
    CandidateStatus,
    DefaultWorkError,
    GateReport,
    GenerationUsage,
    PilotResult,
    RunDefaultPilotCommand,
    RecoverableGeneration,
    SampledSource,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


class DefaultWorkRepository:
    """复用 ModelOsStore 的连接、事务锁和 journal，不另建旁路数据库。"""

    def __init__(self, store) -> None:
        self._store = store

    @property
    def _conn(self):
        conn = self._store._conn
        if conn is None:
            raise RuntimeError("ModelOsStore is not open")
        return conn

    @staticmethod
    def _source_rows(sources: tuple[SampledSource, ...]) -> list[dict[str, Any]]:
        return [
            {
                "uri_at_generation": source.uri_at_generation,
                "memory_id": source.memory_id,
                "sampled_content_hash": source.sampled_content_hash,
                "updated": source.updated,
                "chars": source.chars,
            }
            for source in sources
        ]

    @staticmethod
    def _fingerprint(command: RunDefaultPilotCommand) -> str:
        return _hash(
            _json({"runtime": command.runtime, "source_refs": command.source_refs})
        )

    @staticmethod
    def _identity(sources: tuple[SampledSource, ...]) -> str:
        return _hash(
            _json([POLICY_VERSION, *sorted(s.sampled_content_hash for s in sources)])
        )

    @staticmethod
    def _command_token(command_id: str) -> str:
        return _hash(command_id)

    def _record_intent_in_tx(
        self,
        command: RunDefaultPilotCommand,
        *,
        generation_id: str,
        work_item_id: str,
        sources: tuple[SampledSource, ...],
        stamp: str,
    ) -> None:
        token = self._command_token(command.command_id)
        self._store._insert_event_in_tx(
            EventEnvelope(
                event_id=f"event.default.intent.{token}",
                kind=EventKind.COMMAND_INTENT,
                occurred_at=stamp,
                source="kernel",
                provenance=Provenance.USER_DECISION,
                policy_version=POLICY_VERSION,
                payload={
                    "command_kind": "default.run_pilot",
                    "idempotency_key_hash": f"sha256:{token}",
                    "runtime": command.runtime,
                    "generation_id": generation_id,
                    "automatic_default": False,
                    "sources": self._source_rows(sources),
                },
                work_item_id=work_item_id,
                correlation_id=f"command.default.{token}",
            )
        )

    def _record_result_in_tx(
        self,
        command_id: str,
        *,
        generation_id: str,
        work_item_id: str,
        episode_id: str | None,
        result_code: str,
        candidate_ids: tuple[str, ...] = (),
        unknown: bool = False,
        stamp: str,
    ) -> None:
        token = self._command_token(command_id)
        payload = {
            "command_kind": "default.run_pilot",
            "generation_id": generation_id,
            "candidate_ids": list(candidate_ids),
            "candidate_count": len(candidate_ids),
        }
        payload["unknown_code" if unknown else "result_code"] = result_code
        self._store._insert_event_in_tx(
            EventEnvelope(
                event_id=f"event.default.{'unknown' if unknown else 'result'}.{token}",
                kind=EventKind.COMMAND_UNKNOWN if unknown else EventKind.COMMAND_RESULT,
                occurred_at=stamp,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=POLICY_VERSION,
                payload=payload,
                work_item_id=work_item_id,
                episode_id=episode_id,
                correlation_id=f"command.default.{token}",
            )
        )

    def begin(
        self,
        command: RunDefaultPilotCommand,
        sources: tuple[SampledSource, ...],
        *,
        occurred_at: datetime,
    ) -> BeginGeneration:
        fingerprint = self._fingerprint(command)
        stamp = _iso(occurred_at)
        with self._store._tx():
            existing = self._conn.execute(
                "SELECT fingerprint, generation_id, result_json FROM default_commands "
                "WHERE command_id=?",
                (command.command_id,),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise DefaultWorkError("idempotency_conflict")
                generation = self._generation(existing["generation_id"])
                result = None
                if existing["result_json"] is not None:
                    decoded_result = json.loads(existing["result_json"])
                    error_code = decoded_result.get("error_code")
                    if isinstance(error_code, str):
                        raise DefaultWorkError(error_code)
                    result = self._result_from_json(existing["result_json"])
                return BeginGeneration(
                    generation["work_item_id"], generation["generation_id"], result
                )

            identity = self._identity(sources)
            succeeded = self._conn.execute(
                "SELECT generation_id, work_item_id FROM default_generations "
                "WHERE identity_hash=? AND status='succeeded'",
                (identity,),
            ).fetchone()
            if succeeded is not None:
                result = self._result_for_generation(succeeded["generation_id"])
                result_json = self._result_json(result)
                self._conn.execute(
                    "INSERT INTO default_commands VALUES (?,?,?,?,?,?,?)",
                    (
                        command.command_id,
                        fingerprint,
                        succeeded["generation_id"],
                        command.runtime,
                        _json(command.source_refs),
                        stamp,
                        result_json,
                    ),
                )
                self._record_intent_in_tx(
                    command,
                    generation_id=succeeded["generation_id"],
                    work_item_id=succeeded["work_item_id"],
                    sources=sources,
                    stamp=stamp,
                )
                self._record_result_in_tx(
                    command.command_id,
                    generation_id=result.generation_id,
                    work_item_id=result.work_item_id,
                    episode_id=result.episode_id,
                    result_code="succeeded",
                    candidate_ids=result.candidate_ids,
                    stamp=stamp,
                )
                return BeginGeneration(
                    succeeded["work_item_id"], succeeded["generation_id"], result
                )

            pending = self._conn.execute(
                "SELECT generation_id, work_item_id FROM default_generations "
                "WHERE identity_hash=? AND status IN ('pending','terminal')",
                (identity,),
            ).fetchone()
            if pending is not None:
                generation_id = pending["generation_id"]
                work_item_id = pending["work_item_id"]
            else:
                generation_id = uuid4().hex
                work_item_id = uuid4().hex
                self._insert_work_item(work_item_id, stamp)
                self._conn.execute(
                    "INSERT INTO default_generations "
                    "(generation_id, identity_hash, work_item_id, runtime, requested_tier, "
                    "policy_version, sources_json, status, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        generation_id,
                        identity,
                        work_item_id,
                        command.runtime,
                        "deep",
                        POLICY_VERSION,
                        _json(self._source_rows(sources)),
                        "pending",
                        stamp,
                    ),
                )
            self._conn.execute(
                "INSERT INTO default_commands VALUES (?,?,?,?,?,?,NULL)",
                (
                    command.command_id,
                    fingerprint,
                    generation_id,
                    command.runtime,
                    _json(command.source_refs),
                    stamp,
                ),
            )
            self._record_intent_in_tx(
                command,
                generation_id=generation_id,
                work_item_id=work_item_id,
                sources=sources,
                stamp=stamp,
            )
            return BeginGeneration(work_item_id, generation_id)

    def _insert_work_item(self, work_item_id: str, stamp: str) -> None:
        event = EventEnvelope(
            event_id=f"wi.create.{work_item_id}",
            kind=EventKind.WORK_ITEM_CREATED,
            occurred_at=stamp,
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=POLICY_VERSION,
            payload={
                "work_item_id": work_item_id,
                "kind": WorkItemKind.DEFAULT.value,
                "owner_ref": "system.default",
                "task_id": None,
                "status": WorkItemStatus.PENDING.value,
                "session_purpose": SessionPurpose.DEFAULT.value,
                "memory_eligibility": MemoryEligibility.INELIGIBLE.value,
            },
            work_item_id=work_item_id,
        )
        self._store._insert_event_in_tx(event)

    def attach_episode(self, generation_id: str, episode_id: str) -> None:
        with self._store._tx():
            self._conn.execute(
                "UPDATE default_generations SET episode_id=? "
                "WHERE generation_id=? AND episode_id IS NULL",
                (episode_id, generation_id),
            )

    def record_terminal_output(
        self,
        generation_id: str,
        *,
        episode_id: str,
        effective_model: str,
        validated_output_json: str,
        usage: GenerationUsage,
        occurred_at: datetime,
    ) -> None:
        stamp = _iso(occurred_at)
        with self._store._tx():
            generation = self._generation(generation_id)
            if generation["status"] == "terminal":
                expected = (
                    episode_id,
                    effective_model,
                    validated_output_json,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.wall_seconds,
                    usage.cost,
                )
                actual = (
                    generation["episode_id"],
                    generation["effective_model"],
                    generation["validated_output_json"],
                    generation["input_tokens"],
                    generation["output_tokens"],
                    generation["wall_seconds"],
                    generation["cost"],
                )
                if actual != expected:
                    raise DefaultWorkError("result_unknown")
                return
            if generation["status"] != "pending":
                raise DefaultWorkError("result_unknown")
            self._conn.execute(
                "UPDATE default_generations SET episode_id=?, effective_model=?, "
                "validated_output_json=?, status='terminal', model_called=1, input_tokens=?, "
                "output_tokens=?, "
                "wall_seconds=?, cost=?, completed_at=? WHERE generation_id=?",
                (
                    episode_id,
                    effective_model,
                    validated_output_json,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.wall_seconds,
                    usage.cost,
                    stamp,
                    generation_id,
                ),
            )

    def recoverable(self, generation_id: str) -> RecoverableGeneration | None:
        generation = self._generation(generation_id)
        if (
            generation["status"] != "terminal"
            or not generation["validated_output_json"]
        ):
            return None
        return RecoverableGeneration(
            generation_id=generation_id,
            work_item_id=generation["work_item_id"],
            episode_id=generation["episode_id"],
            runtime=generation["runtime"],
            effective_model=generation["effective_model"],
            validated_output_json=generation["validated_output_json"],
            usage=GenerationUsage(
                int(generation["input_tokens"] or 0),
                int(generation["output_tokens"] or 0),
                float(generation["wall_seconds"] or 0),
                float(generation["cost"]) if generation["cost"] is not None else None,
            ),
        )

    def command_result(self, command_id: str) -> PilotResult | None:
        row = self._conn.execute(
            "SELECT result_json FROM default_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None or row["result_json"] is None:
            return None
        return self._result_from_json(row["result_json"])

    def commit_success(
        self,
        generation_id: str,
        drafts: tuple[CandidateDraft, ...],
        *,
        episode_id: str,
        effective_model: str,
        usage: GenerationUsage,
        occurred_at: datetime,
    ) -> PilotResult:
        stamp = _iso(occurred_at)
        with self._store._tx():
            generation = self._generation(generation_id)
            if generation["status"] == "succeeded":
                return self._result_for_generation(generation_id)
            if generation["status"] not in {"pending", "terminal"}:
                raise DefaultWorkError("result_unknown")
            source_hashes = _json(
                sorted(
                    item["sampled_content_hash"]
                    for item in json.loads(generation["sources_json"])
                )
            )
            for draft in drafts:
                claim_hash = normalized_claim_hash(draft.content)
                existing = self._conn.execute(
                    "SELECT candidate_id FROM default_candidates WHERE "
                    "policy_version=? AND source_hashes_json=? AND normalized_claim_hash=?",
                    (POLICY_VERSION, source_hashes, claim_hash),
                ).fetchone()
                if existing is not None:
                    self._conn.execute(
                        "INSERT INTO default_duplicate_outcomes VALUES (?,?,?,?,?)",
                        (
                            uuid4().hex,
                            generation_id,
                            existing["candidate_id"],
                            claim_hash,
                            stamp,
                        ),
                    )
                    continue
                candidate_id = uuid4().hex
                self._conn.execute(
                    "INSERT INTO default_candidates VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        candidate_id,
                        generation_id,
                        draft.content,
                        _json(draft.source_refs),
                        draft.related_question,
                        draft.why_useful,
                        draft.verification,
                        draft.uncertainty,
                        generation["runtime"],
                        effective_model,
                        "deep",
                        POLICY_VERSION,
                        source_hashes,
                        claim_hash,
                        CandidateStatus.SHOWN.value,
                        stamp,
                        stamp,
                        None,
                        None,
                        None,
                    ),
                )
            self._conn.execute(
                "UPDATE default_generations SET episode_id=?, effective_model=?, "
                "status='succeeded', input_tokens=?, output_tokens=?, wall_seconds=?, "
                "cost=?, completed_at=? WHERE generation_id=?",
                (
                    episode_id,
                    effective_model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.wall_seconds,
                    usage.cost,
                    stamp,
                    generation_id,
                ),
            )
            result = self._result_for_generation(generation_id)
            encoded = self._result_json(result)
            self._conn.execute(
                "UPDATE default_commands SET result_json=? WHERE generation_id=?",
                (encoded, generation_id),
            )
            command_rows = self._conn.execute(
                "SELECT command_id FROM default_commands WHERE generation_id=?",
                (generation_id,),
            ).fetchall()
            for command_row in command_rows:
                self._record_result_in_tx(
                    command_row["command_id"],
                    generation_id=generation_id,
                    work_item_id=result.work_item_id,
                    episode_id=episode_id,
                    result_code="succeeded",
                    candidate_ids=result.candidate_ids,
                    stamp=stamp,
                )
            return result

    def commit_failure(
        self,
        generation_id: str,
        reason: str,
        *,
        episode_id: str | None,
        usage: GenerationUsage | None,
        model_called: bool = True,
        occurred_at: datetime,
    ) -> None:
        stamp = _iso(occurred_at)
        with self._store._tx():
            generation = self._generation(generation_id)
            self._conn.execute(
                "UPDATE default_generations SET episode_id=COALESCE(episode_id,?), "
                "status='failed', failure_reason=?, model_called=?, input_tokens=?, "
                "output_tokens=?, "
                "wall_seconds=?, cost=?, completed_at=? WHERE generation_id=? "
                "AND status IN ('pending','terminal')",
                (
                    episode_id,
                    reason,
                    int(model_called),
                    usage.input_tokens if usage else None,
                    usage.output_tokens if usage else None,
                    usage.wall_seconds if usage else None,
                    usage.cost if usage else None,
                    stamp,
                    generation_id,
                ),
            )
            self._conn.execute(
                "UPDATE default_commands SET result_json=? WHERE generation_id=?",
                (_json({"error_code": reason}), generation_id),
            )
            command_rows = self._conn.execute(
                "SELECT command_id FROM default_commands WHERE generation_id=?",
                (generation_id,),
            ).fetchall()
            for command_row in command_rows:
                self._record_result_in_tx(
                    command_row["command_id"],
                    generation_id=generation_id,
                    work_item_id=generation["work_item_id"],
                    episode_id=episode_id,
                    result_code=reason,
                    unknown=reason == "result_unknown",
                    stamp=stamp,
                )

    def record_outcome(
        self,
        *,
        command_id: str,
        candidate_id: str,
        outcome: str,
        reason: str | None,
        occurred_at: datetime,
    ) -> Candidate:
        if not command_id.strip():
            raise ValueError("command_id must be non-empty")
        if outcome not in {"adopted", "dismissed", "invalid"}:
            raise ValueError("unsupported outcome")
        if outcome == "invalid" and (reason is None or not reason.strip()):
            raise ValueError("invalid outcome requires a reason")
        stamp = _iso(occurred_at)
        fingerprint = _hash(_json([candidate_id, outcome, reason]))
        with self._store._tx():
            previous = self._conn.execute(
                "SELECT fingerprint, candidate_id FROM default_outcome_commands "
                "WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise DefaultWorkError("idempotency_conflict")
                return self._candidate(previous["candidate_id"])
            candidate = self._candidate(candidate_id)
            if candidate.status.is_terminal:
                raise DefaultWorkError("candidate_terminal")
            generation = self._generation(candidate.generation_id)
            token = self._command_token(command_id)
            correlation = f"command.default.outcome.{token}"
            self._store._insert_event_in_tx(
                EventEnvelope(
                    event_id=f"event.default.outcome.intent.{token}",
                    kind=EventKind.COMMAND_INTENT,
                    occurred_at=stamp,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=POLICY_VERSION,
                    payload={
                        "command_kind": "default.candidate_outcome",
                        "idempotency_key_hash": f"sha256:{token}",
                        "candidate_id": candidate_id,
                        "outcome": outcome,
                        "reason": reason,
                    },
                    work_item_id=generation["work_item_id"],
                    episode_id=generation["episode_id"],
                    correlation_id=correlation,
                )
            )
            self._conn.execute(
                "UPDATE default_candidates SET status=?, outcome_at=?, outcome_reason=? "
                "WHERE candidate_id=?",
                (outcome, stamp, reason, candidate_id),
            )
            self._conn.execute(
                "INSERT INTO default_outcome_commands VALUES (?,?,?,?,?,?)",
                (command_id, fingerprint, candidate_id, outcome, reason, stamp),
            )
            self._store._insert_event_in_tx(
                EventEnvelope(
                    event_id=f"event.default.outcome.result.{token}",
                    kind=EventKind.COMMAND_RESULT,
                    occurred_at=stamp,
                    source="kernel",
                    provenance=Provenance.USER_DECISION,
                    policy_version=POLICY_VERSION,
                    payload={
                        "command_kind": "default.candidate_outcome",
                        "candidate_id": candidate_id,
                        "result_code": outcome,
                    },
                    work_item_id=generation["work_item_id"],
                    episode_id=generation["episode_id"],
                    correlation_id=correlation,
                )
            )
            return self._candidate(candidate_id)

    def gate_report(self) -> GateReport:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='adopted' THEN 1 ELSE 0 END) AS adopted, "
            "SUM(CASE WHEN status='invalid' THEN 1 ELSE 0 END) AS invalid, "
            "SUM(CASE WHEN status='dismissed' THEN 1 ELSE 0 END) AS dismissed "
            "FROM default_candidates WHERE status IN ('adopted','dismissed','invalid')"
        ).fetchone()
        usage = self._conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, "
            "SUM(cost) AS known_cost, SUM(CASE WHEN cost IS NULL THEN 1 ELSE 0 END) "
            "AS unknown_cost FROM default_generations WHERE model_called=1"
        ).fetchone()
        total = int(row["total"])
        adopted = int(row["adopted"] or 0)
        invalid = int(row["invalid"] or 0)
        dismissed = int(row["dismissed"] or 0)
        return GateReport(
            status="reevaluation_ready" if total >= 20 else "insufficient_outcomes",
            outcome_count=total,
            adopted=adopted,
            invalid=invalid,
            dismissed=dismissed,
            adoption_rate=(adopted / total if total else None),
            invalid_rate=(invalid / total if total else None),
            total_input_tokens=int(usage["input_tokens"]),
            total_output_tokens=int(usage["output_tokens"]),
            known_cost=(
                float(usage["known_cost"]) if usage["known_cost"] is not None else None
            ),
            unknown_cost_generations=int(usage["unknown_cost"] or 0),
        )

    def generation_view(self, generation_id: str) -> dict[str, Any]:
        generation = self._generation(generation_id)
        return {
            "generation_id": generation["generation_id"],
            "work_item_id": generation["work_item_id"],
            "episode_id": generation["episode_id"],
            "runtime": generation["runtime"],
            "effective_model": generation["effective_model"],
            "tier": generation["requested_tier"],
            "policy_version": generation["policy_version"],
            "sources": json.loads(generation["sources_json"]),
            "status": generation["status"],
            "failure_reason": generation["failure_reason"],
            "usage": {
                "input_tokens": generation["input_tokens"],
                "output_tokens": generation["output_tokens"],
                "wall_seconds": generation["wall_seconds"],
                "cost": generation["cost"],
            },
            "created_at": generation["created_at"],
            "completed_at": generation["completed_at"],
        }

    def generations_for_reconcile(self) -> tuple[dict[str, Any], ...]:
        rows = self._conn.execute(
            "SELECT * FROM default_generations WHERE "
            "status IN ('pending','terminal','succeeded','failed') "
            "ORDER BY created_at, generation_id"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def first_command_id(self, generation_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT command_id FROM default_commands WHERE generation_id=? "
            "ORDER BY occurred_at, command_id LIMIT 1",
            (generation_id,),
        ).fetchone()
        return None if row is None else str(row["command_id"])

    def _generation(self, generation_id: str):
        row = self._conn.execute(
            "SELECT * FROM default_generations WHERE generation_id=?", (generation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(generation_id)
        return row

    def _candidate(self, candidate_id: str) -> Candidate:
        row = self._conn.execute(
            "SELECT * FROM default_candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise DefaultWorkError("candidate_missing")
        return Candidate(
            candidate_id=row["candidate_id"],
            generation_id=row["generation_id"],
            content=row["content"],
            source_refs=tuple(json.loads(row["source_refs_json"])),
            related_question=row["related_question"],
            why_useful=row["why_useful"],
            verification=row["verification"],
            uncertainty=row["uncertainty"],
            runtime=row["runtime"],
            effective_model=row["effective_model"],
            tier=row["tier"],
            policy_version=row["policy_version"],
            status=CandidateStatus(row["status"]),
            created_at=row["created_at"],
            shown_at=row["shown_at"],
            outcome_at=row["outcome_at"],
            outcome_reason=row["outcome_reason"],
            expires_at=row["expires_at"],
        )

    def _result_for_generation(self, generation_id: str) -> PilotResult:
        generation = self._generation(generation_id)
        rows = self._conn.execute(
            "SELECT candidate_id FROM default_candidates WHERE generation_id=? ORDER BY created_at, candidate_id",
            (generation_id,),
        ).fetchall()
        episode_id = generation["episode_id"]
        if not episode_id:
            raise DefaultWorkError("result_unknown")
        return PilotResult(
            generation["work_item_id"],
            episode_id,
            generation_id,
            tuple(self._candidate(row["candidate_id"]) for row in rows),
        )

    @staticmethod
    def _result_json(result: PilotResult) -> str:
        return _json(
            {
                "work_item_id": result.work_item_id,
                "episode_id": result.episode_id,
                "generation_id": result.generation_id,
                "candidate_ids": result.candidate_ids,
            }
        )

    def _result_from_json(self, raw: str) -> PilotResult:
        data = json.loads(raw)
        return PilotResult(
            data["work_item_id"],
            data["episode_id"],
            data["generation_id"],
            tuple(self._candidate(value) for value in data["candidate_ids"]),
        )

    def generation_count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM default_generations").fetchone()[0]
        )

    def pending_candidates(self) -> tuple[Candidate, ...]:
        rows = self._conn.execute(
            "SELECT candidate_id FROM default_candidates "
            "WHERE status IN ('new','shown') ORDER BY created_at, candidate_id"
        ).fetchall()
        return tuple(self._candidate(row["candidate_id"]) for row in rows)

    def candidate_count(self) -> int:
        return int(
            self._conn.execute("SELECT COUNT(*) FROM default_candidates").fetchone()[0]
        )

    def duplicate_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM default_duplicate_outcomes"
            ).fetchone()[0]
        )

    def journal_payload(self, command_id: str) -> str:
        row = self._conn.execute(
            "SELECT source_refs_json FROM default_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        return "" if row is None else str(row["source_refs_json"])
