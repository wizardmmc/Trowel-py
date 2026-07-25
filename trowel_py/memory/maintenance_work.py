"""把既有 memory job 纳入共享 WorkBroker 租约。"""

from __future__ import annotations

import logging
import math
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator

from trowel_py.model_os.work_broker import (
    CatchupPolicy,
    ModelTier,
    UsageRecord,
    WorkBroker,
    WorkDenial,
    WorkKind,
    WorkLease,
    WorkRequest,
)
from trowel_py.quota.types import Provider

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceClaim:
    lease: WorkLease | None
    denial: WorkDenial | None
    _completed: bool = False

    @property
    def granted(self) -> bool:
        return self.denial is None

    def complete(self) -> None:
        if not self.granted:
            raise RuntimeError("denied maintenance work cannot be completed")
        self._completed = True


class MaintenanceLeaseGate:
    """让 scheduler 在 job body 外持有并结算 maintenance lease。"""

    def __init__(
        self,
        broker: WorkBroker | None,
        *,
        provider: Provider = Provider.GLM,
        account_id: str | None = None,
        now_fn: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        renew_interval_seconds: float | None = None,
    ) -> None:
        self._broker = broker
        self._provider = provider
        self._account_id = account_id
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic_fn or time.monotonic
        self._renew_interval = renew_interval_seconds
        self._idle = threading.Condition()
        self._active_claims = 0

    @contextmanager
    def claim(self, scope: str, period: str) -> Iterator[MaintenanceClaim]:
        """领取同 scope/period 的合并租约；只有调用 complete 才推进水位。"""
        if self._broker is None:
            claim = MaintenanceClaim(lease=None, denial=None)
            yield claim
            return

        outcome = self._broker.request(
            WorkRequest(
                kind=WorkKind.MAINTENANCE,
                provider=self._provider,
                account_id=self._account_id,
                model_tier=ModelTier.DEEP,
                catchup=CatchupPolicy.MAINTENANCE_MERGE,
                catchup_scope=scope,
                catchup_period=period,
            )
        )
        if isinstance(outcome, WorkDenial):
            logger.info(
                "[workbroker] maintenance %s/%s denied: %s (%s)",
                scope,
                period,
                outcome.reason.value,
                outcome.detail,
            )
            yield MaintenanceClaim(lease=None, denial=outcome)
            return

        lease = outcome
        try:
            self._broker.begin_call(lease.lease_id, lease.fencing_token)
        except Exception:
            self._broker.release(lease.lease_id, lease.fencing_token)
            raise
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[Exception] = []
        heartbeat = threading.Thread(
            target=self._renew_lease,
            args=(lease, heartbeat_stop, heartbeat_errors),
            name=f"work-lease-{lease.lease_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        with self._idle:
            self._active_claims += 1
        started = self._monotonic()
        claim = MaintenanceClaim(lease=lease, denial=None)
        body_failed = False
        usage_error: Exception | None = None
        try:
            yield claim
        except BaseException:
            body_failed = True
            raise
        finally:
            heartbeat_stop.set()
            heartbeat.join()
            try:
                elapsed = max(0, math.ceil(self._monotonic() - started))
                if heartbeat_errors:
                    usage_error = heartbeat_errors[0]
                else:
                    try:
                        # 一条 calls 代表一次获准执行的 maintenance job；token 与费用未知。
                        self._broker.record_usage(
                            lease.lease_id,
                            lease.fencing_token,
                            UsageRecord(
                                calls=1,
                                cost=None,
                                wall_seconds=elapsed,
                                occurred_at=self._occurred_at(),
                                observation_id=f"{scope}:{period}",
                            ),
                        )
                    except (
                        Exception
                    ) as exc:  # 保留 job 的原始异常，结算错误只作附加诊断。
                        usage_error = exc
                if usage_error is not None:
                    logger.warning(
                        "[workbroker] maintenance %s/%s settlement failed",
                        scope,
                        period,
                        exc_info=(
                            type(usage_error),
                            usage_error,
                            usage_error.__traceback__,
                        ),
                    )

                try:
                    if claim._completed and usage_error is None:
                        if not self._broker.complete(
                            lease.lease_id, lease.fencing_token
                        ):
                            raise RuntimeError(
                                "maintenance lease disappeared before complete: "
                                f"{lease.lease_id}"
                            )
                    else:
                        self._broker.release(lease.lease_id, lease.fencing_token)
                except Exception:
                    if body_failed:
                        logger.warning(
                            "[workbroker] maintenance %s/%s release failed",
                            scope,
                            period,
                            exc_info=True,
                        )
                    else:
                        raise

                if usage_error is not None and not body_failed:
                    raise usage_error
            finally:
                with self._idle:
                    self._active_claims -= 1
                    self._idle.notify_all()

    def wait_for_idle(self, timeout: float) -> bool:
        """等待已交给 worker thread 的 claim 完成结算。"""
        deadline = time.monotonic() + timeout
        with self._idle:
            while self._active_claims:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
            return True

    def _renew_lease(
        self,
        lease: WorkLease,
        stop: threading.Event,
        errors: list[Exception],
    ) -> None:
        assert self._broker is not None
        interval = self._renew_interval
        if interval is None:
            interval = max(1.0, self._broker.policy.lease_ttl_seconds / 3)
        while not stop.wait(interval):
            try:
                self._broker.renew(lease.lease_id, lease.fencing_token)
            except Exception as exc:
                errors.append(exc)
                return

    def _occurred_at(self) -> str:
        observed = self._now()
        if observed.tzinfo is None:
            observed = observed.astimezone()
        return observed.isoformat()
