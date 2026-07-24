"""仲裁 foreground、default 与 maintenance 的模型资源租约。

同一槽每次授权都会递增 fencing token；begin_call、record_usage、
begin_critical_section 与 renew 会拒绝过期或被接管的持有者。
WorkLease 只代表资源所有权，调用方须先取得状态 ownership，再申请 WorkLease。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator

from trowel_py.quota.read_model import QuotaReadModel
from trowel_py.quota.types import Provider, QuotaStatus, QuotaWindowKind

from .lease_codec import cap_to_json as _run_cap_to_json
from .lease_codec import row_to_lease as _run_row_to_lease
from .policy import narrow_cap as _run_narrow_cap
from .policy import parse_iso as _run_parse_iso
from .policy import slot_id as _run_slot_id
from .policy import utc_day as _run_utc_day
from .policy import validate_cap as _run_validate_cap
from .policy import validate_usage as _run_validate_usage
from .schema import SCHEMA_SQL as _SCHEMA_SQL
from .usage_persistence import insert_usage as _insert_usage_in_tx
from .usage_persistence import mark_lease_started as _mark_usage_lease_started_in_tx
from .usage_persistence import observation_seen as _usage_observation_seen_in_tx
from .usage_persistence import totals_in_tx as _run_usage_totals_in_tx

logger = logging.getLogger(__name__)

# 搜索额度与模型调用额度相互独立，不能据此拦截 default 模型工作。
_MODEL_CALL_WINDOW_KINDS: frozenset[QuotaWindowKind] = frozenset(
    {
        QuotaWindowKind.SESSION_5H,
        QuotaWindowKind.WEEKLY,
        QuotaWindowKind.RATE_LIMIT,
    }
)


class WorkKind(Enum):
    """broker 仲裁的工作类别。"""

    FOREGROUND = "foreground"
    DEFAULT = "default"
    MAINTENANCE = "maintenance"


class ModelTier(Enum):
    """请求方预估的档位；当前 broker 只记录，不据此仲裁。"""

    FAST = "fast"
    DEEP = "deep"


class CatchupPolicy(Enum):
    """补跑策略；maintenance 合并同 scope/period 请求，过期 default tick 直接丢弃。"""

    NONE = "none"
    MAINTENANCE_MERGE = "maintenance_merge"
    DEFAULT_DROP = "default_drop"


class DenialReason(Enum):
    """未授予 lease 时选出的主原因。"""

    RATE_LIMIT = "rate_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUOTA_LOW = "quota_low"
    SLOT_BUSY = "slot_busy"
    STALE_TICK_DROPPED = "stale_tick_dropped"
    CATCHUP_ALREADY_DONE = "catchup_already_done"
    NO_ACCOUNT = "no_account"


class StaleWorkLease(Exception):
    """active-lease 门禁发现 lease 已释放、过期或被接管。"""

    def __init__(self, lease_id: str, fencing_token: int) -> None:
        self.lease_id = lease_id
        self.fencing_token = fencing_token
        super().__init__(f"stale work lease: lease_id={lease_id} token={fencing_token}")


class IdempotencyConflict(Exception):
    """同一幂等 key 被用于不同请求指纹。"""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"idempotency_key reused for a different request: {idempotency_key}"
        )


@dataclass(frozen=True)
class BudgetDimensions:
    """预算上限；某轴为 None 表示该轴不限。"""

    calls: int | None = None
    tokens: int | None = None
    cost: float | None = None
    wall_seconds: int | None = None

    def exceeded_by(self, totals: "UsageTotals") -> bool:
        """任一已知轴达到上限即超限；费用未知时只跳过 cost 轴。"""

        if self.calls is not None and totals.calls >= self.calls:
            return True
        if (
            self.tokens is not None
            and (totals.input_tokens + totals.output_tokens) >= self.tokens
        ):
            return True
        if (
            self.cost is not None
            and totals.cost is not None
            and totals.cost >= self.cost
        ):
            return True
        if self.wall_seconds is not None and totals.wall_seconds >= self.wall_seconds:
            return True
        return False


@dataclass(frozen=True)
class BrokerPolicy:
    """带版本的仲裁策略。

    每账号并发大于一时，用量在调用结束后才入账，因此 default cap 是软上限。
    """

    policy_version: str = "workbroker-v0"
    # 内部预算只约束 default。
    default_cap: BudgetDimensions = field(
        default_factory=lambda: BudgetDimensions(calls=100)
    )
    default_quota_used_threshold: float = 80.0
    default_ask_human_on_quota_sensitive: bool = True
    default_tick_max_lag_seconds: int = 3600
    concurrency_per_account: int = 1
    lease_ttl_seconds: int = 600
    glm_account_order: tuple[str, ...] = ("glm-a", "glm-b")
    codex_account_order: tuple[str, ...] = ("codex",)

    def replace(self, **overrides: Any) -> "BrokerPolicy":
        return replace(self, **overrides)

    def account_order(self, provider: Provider) -> tuple[str, ...]:
        if provider is Provider.GLM:
            return self.glm_account_order
        return self.codex_account_order

    def __post_init__(self) -> None:
        if self.lease_ttl_seconds <= 0:
            raise ValueError("BrokerPolicy.lease_ttl_seconds must be positive")
        if self.concurrency_per_account < 1:
            raise ValueError("BrokerPolicy.concurrency_per_account must be >= 1")
        if not math.isfinite(self.default_quota_used_threshold) or not (
            0.0 <= self.default_quota_used_threshold <= 100.0
        ):
            raise ValueError(
                "BrokerPolicy.default_quota_used_threshold must be a finite "
                "percentage in [0, 100]"
            )
        if self.default_tick_max_lag_seconds < 0:
            raise ValueError(
                "BrokerPolicy.default_tick_max_lag_seconds must be non-negative"
            )
        WorkBroker._validate_cap(self.default_cap)
        for acc in self.glm_account_order + self.codex_account_order:
            if not acc or not acc.strip():
                raise ValueError("BrokerPolicy account ids must be non-empty")


@dataclass(frozen=True)
class WorkRequest:
    """模型资源请求。

    priority 与 deadline 是当前同步仲裁不读取的元数据；budget_cap 只能收窄策略上限。
    """

    kind: WorkKind
    provider: Provider
    model_tier: ModelTier = ModelTier.FAST
    account_id: str | None = None
    task_id: str | None = None
    work_item_id: str | None = None
    priority: int = 0
    deadline: str | None = None
    catchup: CatchupPolicy = CatchupPolicy.NONE
    catchup_scope: str | None = None
    catchup_period: str | None = None
    scheduled_for: str | None = None
    budget_cap: BudgetDimensions | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if self.catchup is CatchupPolicy.MAINTENANCE_MERGE:
            if self.kind is not WorkKind.MAINTENANCE:
                raise ValueError(
                    "MAINTENANCE_MERGE catchup is only legal for maintenance work"
                )
            if not (self.catchup_scope and self.catchup_period):
                raise ValueError(
                    "MAINTENANCE_MERGE requires catchup_scope and catchup_period"
                )
        elif self.catchup is CatchupPolicy.DEFAULT_DROP:
            if self.kind is not WorkKind.DEFAULT:
                raise ValueError("DEFAULT_DROP catchup is only legal for default work")
            if not self.scheduled_for:
                raise ValueError("DEFAULT_DROP requires a non-empty scheduled_for")
        if self.scheduled_for is not None:
            WorkBroker._parse_iso(self.scheduled_for)
        if self.deadline is not None:
            WorkBroker._parse_iso(self.deadline)
        if self.budget_cap is not None:
            WorkBroker._validate_cap(self.budget_cap)

    @property
    def fingerprint(self) -> str:
        """幂等身份。

        priority、scheduled_for、deadline、budget_cap 与 model_tier 故意不参与。
        """

        import hashlib

        payload = "|".join(
            (
                self.kind.value,
                self.provider.value,
                self.account_id or "",
                self.task_id or "",
                self.work_item_id or "",
                self.catchup.value,
                self.catchup_scope or "",
                self.catchup_period or "",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkLease:
    """已授予的槽租约；granted_cap 为 None 表示无限，fenced 写须携带 token。"""

    lease_id: str
    slot: str
    provider: Provider
    account_id: str
    work_kind: WorkKind
    model_tier: ModelTier
    granted_cap: BudgetDimensions | None
    acquired_at: str
    expires_at: str
    fencing_token: int
    task_id: str | None
    work_item_id: str | None


@dataclass(frozen=True)
class WorkDenial:
    """未授予的请求及其可区分原因。"""

    reason: DenialReason
    detail: str
    retry_after_seconds: float | None = None
    ask_human: bool = False
    failed_account_id: str | None = None


@dataclass(frozen=True)
class UsageRecord:
    """一次用量观测；归因字段取自 lease，调用方只提供用量与观测身份。"""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    wall_seconds: int | None = None
    occurred_at: str = ""
    observation_id: str | None = None


@dataclass(frozen=True)
class UsageTotals:
    """聚合用量；范围内任一费用未知时，cost 保持 None。"""

    calls: int
    input_tokens: int
    output_tokens: int
    cost: float | None
    wall_seconds: int


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class WorkBroker:
    """仲裁模型资源并持久化租约。

    SQLite 的事务状态属于连接，RLock 因而覆盖每段多步命令及连接生命周期。
    缺少额度快照时不凭空拦截请求。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        policy: BrokerPolicy | None = None,
        read_model: QuotaReadModel | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(db_path)
        self._policy = policy or BrokerPolicy()
        self._read_model = read_model
        self._now = now_fn or _default_now
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        # 限流是 provider 的瞬态事实，重启后重新观测，不写入持久状态。
        self._cooldowns: dict[tuple[Provider, str], float] = {}

    @property
    def policy(self) -> BrokerPolicy:
        return self._policy

    def open(self) -> None:
        with self._lock:
            self._conn = self._create_connection()
            with self._conn:
                self._conn.executescript(_SCHEMA_SQL)
            logger.info(
                "[workbroker] opened (policy=%s, concurrency=%d/slot, ttl=%ds)",
                self._policy.policy_version,
                self._policy.concurrency_per_account,
                self._policy.lease_ttl_seconds,
            )

    def open_recover(self) -> int:
        """打开连接并释放过期 lease；新持有者产生时才递增 fencing token。"""

        self.open()
        return self.recover()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = "IMMEDIATE"
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[None]:
        """开启 IMMEDIATE 事务；连接已在事务中时复用外层边界。"""

        assert self._conn is not None
        if self._conn.in_transaction:
            yield
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._conn.execute("COMMIT")
        except BaseException:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise

    def request(self, req: WorkRequest) -> WorkLease | WorkDenial:
        """在同一提交事务内完成幂等恢复、catch-up gate 与仲裁。"""

        now = self._now()
        with self._lock, self._tx():
            # 重试须先于 catch-up gate，否则活跃 lease 会被误判为已合并。
            reclaimed = self._reclaim_idempotent(req, now)
            if reclaimed is not None:
                return reclaimed
            gate = self._catchup_gate(req, now)
            if gate is not None:
                return gate
            return self._arbitrate_body(req, now)

    def begin_call(self, lease_id: str, fencing_token: int) -> None:
        """发起模型调用前标记 started，关闭 foreground 的抢占窗口。"""

        with self._lock, self._tx():
            assert self._conn is not None
            self._resolve_active_for_write(lease_id, fencing_token)
            self._conn.execute(
                "UPDATE work_leases SET started=1 WHERE lease_id=?", (lease_id,)
            )

    def record_usage(
        self,
        lease_id: str,
        fencing_token: int,
        usage: UsageRecord,
    ) -> UsageTotals:
        """记录用量并返回当日累计；同 lease 内的 observation_id 保持幂等。"""

        self._validate_usage(usage)
        with self._lock, self._tx():
            assert self._conn is not None
            row = self._resolve_active_for_write(lease_id, fencing_token)
            if usage.observation_id is not None:
                if _usage_observation_seen_in_tx(
                    self._conn,
                    lease_id=lease_id,
                    observation_id=usage.observation_id,
                ):
                    day = self._utc_day(usage.occurred_at)
                    return self._totals_in_tx(
                        work_kind=WorkKind(row["work_kind"]),
                        provider=Provider(row["provider"]),
                        account_id=row["account_id"],
                        day=day,
                    )
            _mark_usage_lease_started_in_tx(
                self._conn,
                lease_id=lease_id,
            )
            day = self._utc_day(usage.occurred_at)
            _insert_usage_in_tx(
                self._conn,
                lease_id=lease_id,
                lease_row=row,
                usage=usage,
                day=day,
                policy_version=self._policy.policy_version,
            )
            return self._totals_in_tx(
                work_kind=WorkKind(row["work_kind"]),
                provider=Provider(row["provider"]),
                account_id=row["account_id"],
                day=day,
            )

    def begin_critical_section(self, lease_id: str, fencing_token: int) -> None:
        """仅允许 maintenance 设置预留的临界区标志；当前仲裁尚不依赖该标志。"""

        with self._lock, self._tx():
            assert self._conn is not None
            row = self._resolve_active_for_write(lease_id, fencing_token)
            if row["work_kind"] != WorkKind.MAINTENANCE.value:
                raise ValueError("only maintenance work may enter a critical section")
            self._conn.execute(
                "UPDATE work_leases SET in_critical=1 WHERE lease_id=?", (lease_id,)
            )

    def renew(
        self, lease_id: str, fencing_token: int, *, ttl_seconds: int | None = None
    ) -> WorkLease:
        """延长活跃 lease；已过期的持有者不能借续约复活。"""

        ttl = ttl_seconds if ttl_seconds is not None else self._policy.lease_ttl_seconds
        if ttl <= 0:
            raise ValueError("renew ttl_seconds must be positive")
        with self._lock, self._tx():
            assert self._conn is not None
            row = self._resolve_active_for_write(lease_id, fencing_token)
            now = self._now()
            expires_iso = (now + timedelta(seconds=ttl)).isoformat()
            self._conn.execute(
                "UPDATE work_leases SET expires_at=? WHERE lease_id=?",
                (expires_iso, lease_id),
            )
            return replace(self._row_to_lease(row), expires_at=expires_iso)

    def complete(self, lease_id: str, fencing_token: int) -> bool:
        """推进关联的 maintenance catch-up watermark 后释放槽；无关联时仅释放。"""

        with self._lock, self._tx():
            assert self._conn is not None
            now_iso = self._now().isoformat()
            row = self._conn.execute(
                "SELECT fencing_token FROM work_leases WHERE lease_id=? "
                "AND released_at IS NULL",
                (lease_id,),
            ).fetchone()
            if row is None:
                return False
            if int(row["fencing_token"]) != fencing_token:
                raise StaleWorkLease(lease_id, fencing_token)
            self._conn.execute(
                "UPDATE work_catchup_watermark SET state='completed', "
                "completed_at=? WHERE lease_id=? AND state='claimed'",
                (now_iso, lease_id),
            )
            self._conn.execute(
                "UPDATE work_leases SET released_at=? WHERE lease_id=? "
                "AND released_at IS NULL",
                (now_iso, lease_id),
            )
            return True

    def release(self, lease_id: str, fencing_token: int) -> bool:
        """放弃 lease 而不推进 catch-up；尚未回收的过期 lease 也允许清理。"""

        with self._lock, self._tx():
            assert self._conn is not None
            row = self._conn.execute(
                "SELECT fencing_token FROM work_leases WHERE lease_id=? "
                "AND released_at IS NULL",
                (lease_id,),
            ).fetchone()
            if row is None:
                return False
            if int(row["fencing_token"]) != fencing_token:
                raise StaleWorkLease(lease_id, fencing_token)
            self._conn.execute(
                "UPDATE work_leases SET released_at=? WHERE lease_id=? "
                "AND released_at IS NULL",
                (self._now().isoformat(), lease_id),
            )
            return True

    def report_rate_limit(
        self, provider: Provider, account_id: str, *, cooldown_seconds: int
    ) -> None:
        expiry = self._now().timestamp() + cooldown_seconds
        with self._lock:
            self._cooldowns[(provider, account_id)] = expiry

    def active_leases(self) -> tuple[WorkLease, ...]:
        with self._lock:
            assert self._conn is not None
            now_iso = self._now().isoformat()
            rows = self._conn.execute(
                "SELECT * FROM work_leases WHERE released_at IS NULL "
                "AND expires_at > ? ORDER BY acquired_at",
                (now_iso,),
            ).fetchall()
            return tuple(self._row_to_lease(r) for r in rows)

    def usage_totals(
        self,
        *,
        day: str | None = None,
        work_kind: WorkKind | None = None,
        provider: Provider | None = None,
        account_id: str | None = None,
        task_id: str | None = None,
        model_tier: ModelTier | None = None,
    ) -> UsageTotals:
        with self._lock, self._tx():
            return self._totals_in_tx(
                day=day,
                work_kind=work_kind,
                provider=provider,
                account_id=account_id,
                task_id=task_id,
                model_tier=model_tier,
            )

    def recover(self) -> int:
        with self._lock, self._tx():
            assert self._conn is not None
            now_iso = self._now().isoformat()
            cur = self._conn.execute(
                "UPDATE work_leases SET released_at=? "
                "WHERE released_at IS NULL AND expires_at <= ?",
                (now_iso, now_iso),
            )
            reclaimed = cur.rowcount
        if reclaimed:
            logger.info(
                "[workbroker] recovered %d expired lease(s) left by a prior crash",
                reclaimed,
            )
        return reclaimed

    def _arbitrate_body(
        self, req: WorkRequest, now: datetime
    ) -> WorkLease | WorkDenial:
        candidates = self._candidate_accounts(req, now)
        if not candidates:
            return WorkDenial(DenialReason.NO_ACCOUNT, "no accounts configured")

        outcomes: list[tuple[str, DenialReason]] = []
        earliest_expiry: float | None = None
        for account in candidates:
            if self._in_cooldown(req.provider, account, now):
                outcomes.append((account, DenialReason.RATE_LIMIT))
                continue
            if req.kind is WorkKind.DEFAULT:
                if self._quota_low(req.provider, account):
                    outcomes.append((account, DenialReason.QUOTA_LOW))
                    continue
                if self._budget_exhausted(req, account, now):
                    outcomes.append((account, DenialReason.BUDGET_EXHAUSTED))
                    continue
            lease, busy_expiry = self._try_acquire_any_slot(req, account, now)
            if lease is not None:
                return self._after_grant(req, lease, now)
            outcomes.append((account, DenialReason.SLOT_BUSY))
            if busy_expiry is not None:
                earliest_expiry = (
                    busy_expiry
                    if earliest_expiry is None
                    else min(earliest_expiry, busy_expiry)
                )

        if req.kind is WorkKind.FOREGROUND:
            victim = self._find_preemptable_default(req.provider, candidates)
            if victim is not None:
                self._release_in_tx(victim.lease_id, now)
                lease, _ = self._try_acquire_any_slot(req, victim.account_id, now)
                if lease is not None:
                    return self._after_grant(req, lease, now)

        return self._build_denial(outcomes, earliest_expiry, now)

    def _after_grant(
        self, req: WorkRequest, lease: WorkLease, now: datetime
    ) -> WorkLease | WorkDenial:
        """在仲裁事务内原子写入 lease、幂等映射与 catch-up claim。"""

        assert self._conn is not None
        if req.catchup is CatchupPolicy.MAINTENANCE_MERGE:
            cur = self._conn.execute(
                "INSERT INTO work_catchup_watermark "
                "(scope, period, work_kind, lease_id, state, claimed_at) "
                "VALUES (?,?,?,?, 'claimed', ?) "
                "ON CONFLICT(scope, period, work_kind) DO NOTHING",
                (
                    req.catchup_scope,
                    req.catchup_period,
                    req.kind.value,
                    lease.lease_id,
                    now.isoformat(),
                ),
            )
            if cur.rowcount == 0:
                # 竞争失败必须正常提交撤销；抛异常会连释放一起回滚。
                self._release_in_tx(lease.lease_id, now)
                return WorkDenial(
                    DenialReason.CATCHUP_ALREADY_DONE,
                    f"lost catch-up race for {req.catchup_scope}/"
                    f"{req.catchup_period}; merged",
                )
        if req.idempotency_key is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO work_idempotency_keys "
                "(idempotency_key, lease_id, fingerprint, created_at) VALUES (?,?,?,?)",
                (req.idempotency_key, lease.lease_id, req.fingerprint, now.isoformat()),
            )
        return lease

    def _candidate_accounts(self, req: WorkRequest, now: datetime) -> tuple[str, ...]:
        """列出候选账号；指定账号时禁用 failover。"""

        if req.account_id is not None:
            return (req.account_id,)
        base = self._policy.account_order(req.provider)
        if req.kind is WorkKind.DEFAULT or self._read_model is None:
            return base
        # 未知额度独立成第三态，避免无数据账号排在已知健康账号之前。
        order = {"healthy": 0, "unknown": 1, "low": 2}
        return tuple(
            sorted(base, key=lambda acc: order[self._quota_state(req.provider, acc)])
        )

    def _catchup_gate(self, req: WorkRequest, now: datetime) -> WorkDenial | None:
        if req.catchup is CatchupPolicy.DEFAULT_DROP:
            if req.scheduled_for is not None:
                scheduled = self._parse_iso(req.scheduled_for)
                lag = (now - scheduled).total_seconds()
                if lag > self._policy.default_tick_max_lag_seconds:
                    return WorkDenial(
                        DenialReason.STALE_TICK_DROPPED,
                        f"default tick {lag:.0f}s old (> "
                        f"{self._policy.default_tick_max_lag_seconds}s lag); dropped",
                    )
        elif req.catchup is CatchupPolicy.MAINTENANCE_MERGE:
            assert req.catchup_scope and req.catchup_period
            if self._catchup_seen(req.catchup_scope, req.catchup_period, req.kind, now):
                return WorkDenial(
                    DenialReason.CATCHUP_ALREADY_DONE,
                    f"maintenance catch-up already done/live for "
                    f"{req.catchup_scope}/{req.catchup_period}; merged",
                )
        return None

    def _try_acquire_any_slot(
        self, req: WorkRequest, account: str, now: datetime
    ) -> tuple[WorkLease | None, float | None]:
        earliest: float | None = None
        for idx in range(self._policy.concurrency_per_account):
            slot = self._slot_id(req.provider, account, idx)
            lease = self._cas_acquire(slot, req, account, now)
            if lease is not None:
                return lease, None
            expiry = self._active_holder_expiry(slot, now)
            if expiry is not None and (earliest is None or expiry < earliest):
                earliest = expiry
        return None, earliest

    def _cas_acquire(
        self, slot: str, req: WorkRequest, account: str, now: datetime
    ) -> WorkLease | None:
        """获取单个槽；接管过期持有者后生成严格递增的 fencing token。"""

        assert self._conn is not None
        now_iso = now.isoformat()
        expires_iso = (
            now + timedelta(seconds=self._policy.lease_ttl_seconds)
        ).isoformat()
        holder = self._conn.execute(
            "SELECT * FROM work_leases WHERE slot=? AND released_at IS NULL",
            (slot,),
        ).fetchone()
        if holder is not None:
            if holder["expires_at"] > now_iso:
                return None
            self._conn.execute(
                "UPDATE work_leases SET released_at=? WHERE lease_id=?",
                (now_iso, holder["lease_id"]),
            )
        token = self._next_fence_in_tx(slot)
        lease_id = uuid.uuid4().hex
        granted_cap = self._decide_granted_cap(req)
        self._conn.execute(
            "INSERT INTO work_leases (lease_id, slot, provider, account_id, "
            "work_kind, model_tier, task_id, work_item_id, granted_cap, "
            "started, in_critical, acquired_at, expires_at, fencing_token, "
            "idempotency_key, policy_version, released_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?)",
            (
                lease_id,
                slot,
                req.provider.value,
                account,
                req.kind.value,
                req.model_tier.value,
                req.task_id,
                req.work_item_id,
                self._cap_to_json(granted_cap),
                now_iso,
                expires_iso,
                token,
                req.idempotency_key,
                self._policy.policy_version,
                None,
            ),
        )
        return WorkLease(
            lease_id=lease_id,
            slot=slot,
            provider=req.provider,
            account_id=account,
            work_kind=req.kind,
            model_tier=req.model_tier,
            granted_cap=granted_cap,
            acquired_at=now_iso,
            expires_at=expires_iso,
            fencing_token=token,
            task_id=req.task_id,
            work_item_id=req.work_item_id,
        )

    def _find_preemptable_default(
        self, provider: Provider, accounts: tuple[str, ...]
    ) -> WorkLease | None:
        """选择最早、尚未 begin_call 且非临界的 default lease 供 foreground 抢占。"""

        assert self._conn is not None
        placeholders = ",".join("?" for _ in accounts)
        row = self._conn.execute(
            f"SELECT * FROM work_leases WHERE released_at IS NULL "
            f"AND work_kind='default' AND started=0 AND in_critical=0 "
            f"AND provider=? AND account_id IN ({placeholders}) "
            f"ORDER BY acquired_at LIMIT 1",
            (provider.value, *accounts),
        ).fetchone()
        return self._row_to_lease(row) if row is not None else None

    def _release_in_tx(self, lease_id: str, now: datetime) -> None:
        assert self._conn is not None
        self._conn.execute(
            "UPDATE work_leases SET released_at=? WHERE lease_id=? "
            "AND released_at IS NULL",
            (now.isoformat(), lease_id),
        )

    def _build_denial(
        self,
        outcomes: list[tuple[str, DenialReason]],
        earliest_expiry: float | None,
        now: datetime,
    ) -> WorkDenial:
        """按 quota、限流、预算、忙槽的优先级汇总账号失败原因。"""

        retry_after = self._retry_after(earliest_expiry, now)
        detail = " ".join(f"{acct}:{r.value}" for acct, r in outcomes)
        for reason in (
            DenialReason.QUOTA_LOW,
            DenialReason.RATE_LIMIT,
            DenialReason.BUDGET_EXHAUSTED,
            DenialReason.SLOT_BUSY,
        ):
            hits = [acct for acct, r in outcomes if r is reason]
            if hits:
                ask = (
                    reason is DenialReason.QUOTA_LOW
                    and self._policy.default_ask_human_on_quota_sensitive
                )
                return WorkDenial(
                    reason,
                    detail,
                    retry_after_seconds=retry_after,
                    ask_human=ask,
                    failed_account_id=hits[0],
                )
        return WorkDenial(
            DenialReason.SLOT_BUSY,
            "no candidate slot available",
            retry_after_seconds=retry_after,
        )

    def _in_cooldown(self, provider: Provider, account: str, now: datetime) -> bool:
        expiry = self._cooldowns.get((provider, account))
        if expiry is None:
            return False
        if now.timestamp() >= expiry:
            self._cooldowns.pop((provider, account), None)
            return False
        return True

    def _quota_low(self, provider: Provider, account: str) -> bool:
        return self._quota_state(provider, account) == "low"

    def _quota_state(self, provider: Provider, account: str) -> str:
        """返回额度三态；缺失或非 OK 的快照为 unknown，运行时限流另由 cooldown 表示。"""

        if self._read_model is None:
            return "unknown"
        snap = self._read_model.get(provider, account)
        if snap is None or snap.status is not QuotaStatus.OK:
            return "unknown"
        if any(
            window.used_percent >= self._policy.default_quota_used_threshold
            for window in snap.windows
            if window.kind in _MODEL_CALL_WINDOW_KINDS
        ):
            return "low"
        return "healthy"

    def _budget_exhausted(self, req: WorkRequest, account: str, now: datetime) -> bool:
        cap = self._narrow_cap(self._policy.default_cap, req.budget_cap)
        totals = self._totals_in_tx(
            work_kind=WorkKind.DEFAULT,
            provider=req.provider,
            account_id=account,
            day=now.date().isoformat(),
        )
        return cap.exceeded_by(totals)

    def _resolve_active_for_write(
        self, lease_id: str, fencing_token: int
    ) -> sqlite3.Row:
        """加载 fenced 写的活跃 lease，拒绝无效 token、释放和过期状态。"""

        assert self._conn is not None
        now_iso = self._now().isoformat()
        row = self._conn.execute(
            "SELECT * FROM work_leases WHERE lease_id=? AND released_at IS NULL",
            (lease_id,),
        ).fetchone()
        if row is None:
            raise StaleWorkLease(lease_id, fencing_token)
        if int(row["fencing_token"]) != fencing_token:
            raise StaleWorkLease(lease_id, fencing_token)
        if row["expires_at"] <= now_iso:
            raise StaleWorkLease(lease_id, fencing_token)
        return row

    def _reclaim_idempotent(self, req: WorkRequest, now: datetime) -> WorkLease | None:
        """复用幂等 key 对应的活跃 lease；过期映射清理后重新仲裁。"""

        assert self._conn is not None
        if req.idempotency_key is None:
            return None
        now_iso = now.isoformat()
        mapped = self._conn.execute(
            "SELECT lease_id, fingerprint FROM work_idempotency_keys "
            "WHERE idempotency_key=?",
            (req.idempotency_key,),
        ).fetchone()
        if mapped is None:
            return None
        if mapped["fingerprint"] != req.fingerprint:
            raise IdempotencyConflict(req.idempotency_key)
        row = self._conn.execute(
            "SELECT * FROM work_leases WHERE lease_id=? AND released_at IS NULL",
            (mapped["lease_id"],),
        ).fetchone()
        if row is None:
            self._conn.execute(
                "DELETE FROM work_idempotency_keys WHERE idempotency_key=?",
                (req.idempotency_key,),
            )
            return None
        if row["expires_at"] <= now_iso:
            self._conn.execute(
                "UPDATE work_leases SET released_at=? WHERE lease_id=?",
                (now_iso, row["lease_id"]),
            )
            self._conn.execute(
                "DELETE FROM work_idempotency_keys WHERE idempotency_key=?",
                (req.idempotency_key,),
            )
            return None
        return self._row_to_lease(row)

    def _next_fence_in_tx(self, slot: str) -> int:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT last_token FROM work_fence_counters WHERE slot=?", (slot,)
        ).fetchone()
        new_token = (int(row["last_token"]) + 1) if row is not None else 1
        self._conn.execute(
            "INSERT INTO work_fence_counters (slot, last_token) VALUES (?, ?) "
            "ON CONFLICT(slot) DO UPDATE SET last_token=excluded.last_token",
            (slot, new_token),
        )
        return new_token

    def _active_holder_expiry(self, slot: str, now: datetime) -> float | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT expires_at FROM work_leases WHERE slot=? AND released_at IS NULL "
            "AND expires_at > ?",
            (slot, now.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["expires_at"]).timestamp()

    def _retry_after(
        self, earliest_expiry: float | None, now: datetime
    ) -> float | None:
        if earliest_expiry is None:
            return None
        return max(0.1, earliest_expiry - now.timestamp())

    def _catchup_seen(
        self, scope: str, period: str, kind: WorkKind, now: datetime
    ) -> bool:
        """识别已完成或在途的 catch-up；清除失去活跃 lease 的 claim 以允许重跑。"""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT state, lease_id FROM work_catchup_watermark "
            "WHERE scope=? AND period=? AND work_kind=?",
            (scope, period, kind.value),
        ).fetchone()
        if row is None:
            return False
        if row["state"] == "completed":
            return True
        lease = self._conn.execute(
            "SELECT 1 FROM work_leases WHERE lease_id=? AND released_at IS NULL "
            "AND expires_at > ?",
            (row["lease_id"], now.isoformat()),
        ).fetchone()
        if lease is not None:
            return True
        self._conn.execute(
            "DELETE FROM work_catchup_watermark WHERE scope=? AND period=? "
            "AND work_kind=?",
            (scope, period, kind.value),
        )
        return False

    def _totals_in_tx(
        self,
        *,
        day: str | None = None,
        work_kind: WorkKind | None = None,
        provider: Provider | None = None,
        account_id: str | None = None,
        task_id: str | None = None,
        model_tier: ModelTier | None = None,
    ) -> UsageTotals:
        """在当前事务内聚合用量；任一费用未知时 cost 保持 None。"""

        assert self._conn is not None
        return _run_usage_totals_in_tx(
            self._conn,
            day=day,
            work_kind=work_kind,
            provider=provider,
            account_id=account_id,
            task_id=task_id,
            model_tier=model_tier,
            totals_factory=UsageTotals,
        )

    def _decide_granted_cap(self, req: WorkRequest) -> BudgetDimensions | None:
        if req.kind is WorkKind.DEFAULT:
            return self._narrow_cap(self._policy.default_cap, req.budget_cap)
        return None

    @staticmethod
    def _narrow_cap(
        policy_cap: BudgetDimensions, req_cap: BudgetDimensions | None
    ) -> BudgetDimensions:
        """逐轴选择更严格的上限；None 在该轴表示无限。"""

        return _run_narrow_cap(
            policy_cap,
            req_cap,
            budget_type=BudgetDimensions,
            min_fn=lambda left, right: min(left, right),
        )

    @staticmethod
    def _slot_id(provider: Provider, account: str, idx: int) -> str:
        return _run_slot_id(provider, account, idx)

    @staticmethod
    def _cap_to_json(cap: BudgetDimensions | None) -> str | None:
        return _run_cap_to_json(cap, dumps=json.dumps)

    def _row_to_lease(self, row: sqlite3.Row) -> WorkLease:
        return _run_row_to_lease(
            row,
            loads=json.loads,
            budget_dimensions_type=BudgetDimensions,
            work_lease_type=WorkLease,
            provider_type=Provider,
            work_kind_type=WorkKind,
            model_tier_type=ModelTier,
        )

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        return _run_parse_iso(
            value,
            fromisoformat=lambda candidate: datetime.fromisoformat(candidate),
            utc_resolver=lambda: timezone.utc,
        )

    @staticmethod
    def _utc_day(occurred_at: str) -> str:
        return _run_utc_day(
            occurred_at,
            parse_iso=lambda candidate: WorkBroker._parse_iso(candidate),
        )

    @staticmethod
    def _validate_cap(cap: BudgetDimensions) -> None:
        """拒绝 bool、负数与非有限值，避免 NaN 绕过范围比较。"""

        _run_validate_cap(
            cap,
            isinstance_resolver=lambda: isinstance,
            bool_type_resolver=lambda: bool,
            int_type_resolver=lambda: int,
            number_types_resolver=lambda: int | float,
            isfinite_resolver=lambda: math.isfinite,
        )

    @staticmethod
    def _validate_usage(usage: UsageRecord) -> None:
        _run_validate_usage(
            usage,
            parse_iso_resolver=lambda: WorkBroker._parse_iso,
            isfinite_resolver=lambda: math.isfinite,
        )
