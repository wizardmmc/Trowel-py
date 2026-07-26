"""把统一 AgentEvent 转成 L03 已冻结的认知信号。"""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from trowel_py.model_os.cognitive_signals import (
    AttemptBinding,
    AttemptComparisonKey,
    AttemptRef,
    CognitiveSignalDraft,
    EvidenceAuthority,
    EvidenceRef,
    InMemorySignalAuthorityRegistry,
    PointFact,
    Reliability,
    SignalFamily,
    SignalKind,
    SignalNormalizationContext,
    ValidatorInvocationEvidence,
)
from trowel_py.model_os.signal_normalizer import normalize_and_classify
from trowel_py.model_os.validator_intent import (
    build_validator_outcome,
    match_validator_intent_from_shell,
    normalize_validator_target,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _runtime(value: str) -> str:
    return "cc" if value == "claude_code" else value


@dataclass(frozen=True)
class _Attempt:
    attempt_id: str
    runtime: str
    task_id: str | None
    episode_id: str
    generation: str


@dataclass(frozen=True)
class _ValidatorCall:
    attempt_id: str
    runtime: str
    argv: tuple[str, ...]
    intent: Any


class CognitiveSignalBridge:
    def __init__(self, store, authority: InMemorySignalAuthorityRegistry) -> None:
        self._store = store
        self._authority = authority
        self._attempts: dict[str, _Attempt] = {}
        self._validators: dict[tuple[str, str], _ValidatorCall] = {}
        authority.trust_adapter("agent-event.cc", "cc")
        authority.trust_adapter("agent-event.codex", "codex")

    def observe(
        self,
        event: Mapping[str, Any],
        *,
        runtime_generation: str,
    ) -> None:
        session_id = str(event.get("session_id") or "")
        runtime = _runtime(str(event.get("runtime") or ""))
        event_type = str(event.get("type") or "")
        if not session_id or runtime not in {"cc", "codex"}:
            return
        binding = self._store.episode_runtime_binding_for_session(session_id)
        if binding is None or binding.runtime_generation != runtime_generation:
            return
        episode = self._store.read_snapshot().episode_by_id(binding.episode_id)
        if episode is None:
            return
        if event_type == "turn_start":
            turn_id = event.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id:
                return
            attempt_id = "attempt." + _token(
                f"{episode.episode_id}:{runtime_generation}:{turn_id}"
            )
            attempt = _Attempt(
                attempt_id,
                runtime,
                episode.task_id,
                episode.episode_id,
                runtime_generation,
            )
            self._attempts[session_id] = attempt
            self._authority.register_attempt(
                AttemptBinding(
                    attempt_id=attempt_id,
                    runtime=runtime,
                    task_id=episode.task_id,
                    episode_id=episode.episode_id,
                    native_session_id=binding.native_session_id or session_id,
                    binding_generation=runtime_generation,
                )
            )
            return
        current_attempt = self._attempts.get(session_id)
        if current_attempt is None or current_attempt.generation != runtime_generation:
            return
        if event_type == "tool_call":
            self._remember_validator(session_id, event, current_attempt)
            return
        if event_type == "tool_result":
            self._record_validator(session_id, event, current_attempt)
        raw = self._raw_observation(event_type, event.get("payload"), runtime)
        if raw is None:
            return
        ref = self._event_ref(event, current_attempt)
        self._authority.register_reference(ref, attempt_id=current_attempt.attempt_id)
        context = SignalNormalizationContext(
            attempt_id=current_attempt.attempt_id,
            subject=AttemptRef(current_attempt.attempt_id),
            comparison_key=AttemptComparisonKey(
                runtime=runtime,
                attempt_category="turn:main",
                task_id=current_attempt.task_id,
                target_ref=None,
            ),
            evidence_refs=(ref,),
            native_event_id=ref.ref_id,
            observation_ordinal=0,
            observed_at=_now_iso(),
            binding_generation=runtime_generation,
        )
        draft = normalize_and_classify(raw, context)
        if draft is not None:
            self._store.record_runtime_observation(
                draft,
                adapter_identity=f"agent-event.{runtime}",
                binding_generation=runtime_generation,
            )
        if event_type in {"finished", "error", "interrupted", "session_exited"}:
            self._attempts.pop(session_id, None)
            self._validators = {
                key: value
                for key, value in self._validators.items()
                if key[0] != session_id
            }

    def _event_ref(self, event: Mapping[str, Any], attempt: _Attempt) -> EvidenceRef:
        identity = ":".join(
            (
                str(event.get("session_id")),
                attempt.generation,
                str(event.get("seq")),
                str(event.get("type")),
                str(event.get("item_id") or "none"),
            )
        )
        return EvidenceRef(
            namespace=attempt.runtime,
            ref_id="agent-event." + _token(identity),
            runtime=attempt.runtime,
            event_kind=str(event.get("type") or "unknown"),
            invocation_id=attempt.attempt_id,
        )

    @staticmethod
    def _raw_observation(
        event_type: str,
        payload_value: object,
        runtime: str,
    ) -> dict[str, Any] | None:
        payload = dict(payload_value) if isinstance(payload_value, Mapping) else {}
        if event_type == "finished":
            if runtime == "cc":
                return {"type": "result", "subtype": "success", "is_error": False}
            return {"method": "turn/completed", "status": "completed", "error": None}
        if event_type in {"error", "interrupted", "session_exited"}:
            if runtime == "cc":
                return {"type": "result", "subtype": "error", "is_error": True}
            return {"event_type": "error", "will_retry": False}
        if event_type == "retrying":
            if runtime == "cc":
                return {
                    "type": "system",
                    "subtype": "api_retry",
                    "attempt": payload.get("attempt"),
                    "max_retries": payload.get("max_retries"),
                    "retry_delay_ms": payload.get("retry_delay_ms"),
                }
            return {"method": "error", "will_retry": True}
        if event_type == "tool_result":
            exit_code = payload.get("exit_code")
            failed = (
                payload.get("is_error") is True
                or (isinstance(exit_code, int) and exit_code != 0)
                or payload.get("status") == "failed"
            )
            return {
                "type": "tool_result",
                "is_error": failed,
                "timed_out": payload.get("timed_out"),
                "message": payload.get("content") or "",
            }
        if event_type in {"approval_request", "elicit_request"}:
            return {"type": "control_request"}
        return None

    def _remember_validator(
        self,
        session_id: str,
        event: Mapping[str, Any],
        attempt: _Attempt,
    ) -> None:
        item_id = event.get("item_id")
        payload = event.get("payload")
        if not isinstance(item_id, str) or not isinstance(payload, Mapping):
            return
        raw_input = payload.get("input")
        command = raw_input.get("command") if isinstance(raw_input, Mapping) else None
        if not isinstance(command, str):
            return
        intent = match_validator_intent_from_shell(command)
        if intent is None:
            return
        try:
            argv = tuple(shlex.split(command, posix=True))
        except ValueError:
            return
        self._validators[(session_id, item_id)] = _ValidatorCall(
            attempt.attempt_id,
            attempt.runtime,
            argv,
            intent,
        )

    def _record_validator(
        self,
        session_id: str,
        event: Mapping[str, Any],
        attempt: _Attempt,
    ) -> None:
        item_id = event.get("item_id")
        if not isinstance(item_id, str):
            return
        call = self._validators.pop((session_id, item_id), None)
        payload = event.get("payload")
        if call is None or not isinstance(payload, Mapping):
            return
        exit_code = payload.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            if payload.get("is_error") is False:
                exit_code = 0
            else:
                return
        base_ref = self._event_ref(event, attempt)
        exit_ref = EvidenceRef(
            attempt.runtime,
            base_ref.ref_id + ".exit",
            runtime=attempt.runtime,
            invocation_id=item_id,
        )
        output_ref = EvidenceRef(
            attempt.runtime,
            base_ref.ref_id + ".output",
            runtime=attempt.runtime,
            invocation_id=item_id,
        )
        self._authority.register_reference(
            exit_ref,
            attempt_id=attempt.attempt_id,
            authority=EvidenceAuthority.VALIDATOR_EXIT,
        )
        self._authority.register_reference(
            output_ref,
            attempt_id=attempt.attempt_id,
            authority=EvidenceAuthority.VALIDATOR_OUTPUT,
        )
        self._authority.register_validator_invocation(
            ValidatorInvocationEvidence(
                native_tool_item_id=item_id,
                intent_id=call.intent.intent_id,
                attempt_id=attempt.attempt_id,
                runtime=attempt.runtime,
                normalized_argv=call.argv,
                exit_event_ref=exit_ref,
                output_ref=output_ref,
                exit_code=exit_code,
                completed=True,
            )
        )
        validator_payload = build_validator_outcome(
            intent=call.intent,
            argv=call.argv,
            exit_event_ref=exit_ref,
            output_ref=output_ref,
            native_tool_item_id=item_id,
            exit_code=exit_code,
            completed=True,
        )
        if validator_payload is None:
            return
        target = normalize_validator_target(call.intent, call.argv)
        draft = CognitiveSignalDraft(
            native_event_id=base_ref.ref_id,
            observation_ordinal=1,
            kind=SignalKind(
                SignalFamily.VALIDATOR_OUTCOME,
                "pass" if exit_code == 0 else "fail",
            ),
            reliability=Reliability.RELIABLE,
            validity=PointFact(),
            subject=AttemptRef(attempt.attempt_id),
            attempt_id=attempt.attempt_id,
            comparison_key=AttemptComparisonKey(
                runtime=attempt.runtime,
                attempt_category=f"validator:{call.intent.intent_id}",
                task_id=attempt.task_id,
                target_ref=target,
                validator_intent_id=call.intent.intent_id,
            ),
            payload=validator_payload,
            evidence_refs=(exit_ref, output_ref),
            observed_at=_now_iso(),
            normalizer_version="normalizer-v1",
            comparison_key_version="comparison-v1",
        )
        self._store.record_runtime_observation(
            draft,
            adapter_identity=f"agent-event.{attempt.runtime}",
            binding_generation=attempt.generation,
        )
