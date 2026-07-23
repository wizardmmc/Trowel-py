"""WorkBroker：foreground / default / maintenance 三类工作共享的模型资源仲裁器。

职责：把 provider 配额、预算、并发槽以租约（`WorkLease`）的形式租给三类工作，
让前台任务、默认态、nightly 维护（memory review / profile distill / tidy）不再互抢额度。

输入：`request(WorkRequest)` 由各 scheduler / 调用方在执行模型工作前调用。
关键转换：按账号 failover + 优先级/预算/配额闸门 + 并发槽 CAS，决定 grant / deny / defer。
输出：成功给 `WorkLease`（持有者凭 `fencing_token` 记账/续约/释放），失败给 `WorkDenial`。
最重要不变量：fencing——每个槽每次新 grant 的 token 严格递增，过期 lease 在下次 acquire 被接管，
且 `begin_call` / `record_usage` / `begin_critical_section` 等 fenced 写一律拒绝过期 lease，
旧持有者的 token 永远记不了账、占不住槽。

冻结的设计决定（别再翻案）：

- foreground 不设内部预算上限。它的刹车是上下文满 / 用户抢占 / watchdog / provider 限流，
  不是 Trowel 自造的数字。跑飞的 agent loop 由 watchdog 拦，不靠 cap。
- default 是唯一的预算闸门。只有它「不该烧稀缺额度」，所以闸门长在它身上：
  内部每日 cap + 外部额度阈值（来自 `quota.read_model`）。
  请求只能把 policy cap 收窄，不能放宽（预算超限不允许模型自行加额）。
- maintenance 是必要工作，不可抢占。漏跑的 maintenance 原子 claim 一个 `(scope, period)`，
  成功 `complete` 才算完成；崩溃留下的 claim（lease 已死、未完成）会在下次请求自愈清掉。
  漏掉的 default tick 直接丢弃，不补烧整夜。
- provider 额度按账号独立建模（GLM-A / GLM-B / Codex 是独立槽、独立额度池）；
  failover 走 policy 顺序（foreground / maintenance 按健康度优先）。
- rate-limit 是 provider 运行时事实，不是预算耗尽。运行时撞 429 走 `report_rate_limit`；
  broker 绝不把额度快照的「满了」当成 rate-limit。

槽 CAS + fencing token 接管复刻了 `model_os.store` 里 `acquire_lease` /
`_takeover_or_conflict` / `_next_fence_token_in_tx` 的成熟模式，但在这里重新实现、
不复用 ownership lease 表：work 槽语义不同（每账号 N 个、可抢占、holding↔running 相位），
broker 还要可注入时钟做 fake-clock 测试；复用 ownership lease 表会带来不可注入的时钟和双写一致性隐患。

`WorkLease` 和 model_os 里的 ownership lease 是两种不同的 lease：前者是资源所有权
（provider/预算/并发），后者是状态所有权（Episode/Task）。获取顺序——先 ownership、再 work lease——
是调用方契约，broker 不强制，甚至不需要知道 Episode 的存在。
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

logger = logging.getLogger(__name__)

#: 代表「模型调用额度」的窗口类型（default 闸门保护的稀缺资源）。
#: WEB_SEARCHES_MONTHLY 是另一种预算——搜索额度满了不该拦 default 的模型工作。
_MODEL_CALL_WINDOW_KINDS: frozenset[QuotaWindowKind] = frozenset(
    {
        QuotaWindowKind.SESSION_5H,
        QuotaWindowKind.WEEKLY,
        QuotaWindowKind.RATE_LIMIT,
    }
)


# ----------------------------------------------------------------------- types


class WorkKind(Enum):
    """broker 仲裁的三类工作。

    - `FOREGROUND`：用户在推进的任务，不设内部预算上限。
    - `DEFAULT`：空闲自由联想，唯一受预算闸门约束的类别。
    - `MAINTENANCE`：nightly review / distill / tidy，必要工作、不可抢占。
    """

    FOREGROUND = "foreground"
    DEFAULT = "default"
    MAINTENANCE = "maintenance"


class ModelTier(Enum):
    """工作预估要用的脑力档位（真正选档由 router 决定，这里只是请求方估计）。"""

    FAST = "fast"
    DEEP = "deep"


class CatchupPolicy(Enum):
    """对「本该早就触发、现在才来」的工作怎么处理。

    - `NONE`：不是补跑，是实时请求。
    - `MAINTENANCE_MERGE`：漏掉的 maintenance，原子 claim 一个 `(scope, period)`，
      第二次请求被合并丢弃；只能配 `WorkKind.MAINTENANCE`。
    - `DEFAULT_DROP`：漏掉的 default tick，超过 policy 滞后阈值就丢弃、不补；
      只能配 `WorkKind.DEFAULT`。
    """

    NONE = "none"
    MAINTENANCE_MERGE = "maintenance_merge"
    DEFAULT_DROP = "default_drop"


class DenialReason(Enum):
    """请求没拿到 lease 的原因。各类原因状态互斥，调用方要能区分。

    - `RATE_LIMIT`：provider 运行时限流 / cooldown（provider 的事实）。
    - `BUDGET_EXHAUSTED`：今日 default 用量已达内部 cap。
    - `QUOTA_LOW`：外部额度（read model）已用% ≥ 阈值（仅 default）。
    - `SLOT_BUSY`：候选槽全被不可抢占的工作占着。
    - `STALE_TICK_DROPPED`：default 补跑 tick 超过 policy 滞后阈值被丢弃。
    - `CATCHUP_ALREADY_DONE`：该 maintenance `(scope, period)` 已完成或正在跑。
    - `NO_ACCOUNT`：该 provider 没配任何账号。
    """

    RATE_LIMIT = "rate_limit"
    BUDGET_EXHAUSTED = "budget_exhausted"
    QUOTA_LOW = "quota_low"
    SLOT_BUSY = "slot_busy"
    STALE_TICK_DROPPED = "stale_tick_dropped"
    CATCHUP_ALREADY_DONE = "catchup_already_done"
    NO_ACCOUNT = "no_account"


class StaleWorkLease(Exception):
    """fenced 写带了一个不属于当前活跃持有者的 token——lease 已释放、被接管，
    或已过期（TTL 到了还没被扫）。

    抛出而非静默忽略，让旧的 runner 知道自己已不持有槽：它那次在途模型调用的结果
    不能记账，也不能标临界区。`release` 是唯一例外——释放一个过期/已释放的 lease
    是无害清理（返回 False）。"""

    def __init__(self, lease_id: str, fencing_token: int) -> None:
        self.lease_id = lease_id
        self.fencing_token = fencing_token
        super().__init__(
            f"stale work lease: lease_id={lease_id} token={fencing_token}"
        )


class IdempotencyConflict(Exception):
    """重试 key 被复用在另一个不同的请求上。

    key 是一个调用方对**一次**获取的 重试身份；把它绑定到请求 fingerprint，
    能抓出跨请求（不同 kind/account/task/catchup）复用同一 key 的调用方，
    而不是静默返回上一次那份对不上的 lease。"""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"idempotency_key reused for a different request: {idempotency_key}"
        )


@dataclass(frozen=True)
class BudgetDimensions:
    """一组预算维度（调用次数 / token / 费用 / 墙钟）。

    某字段为 `None` 表示「该轴不限」。全 `None` 的实例是无限额度的哨兵，
    用于 foreground / maintenance。既作 policy cap（`BrokerPolicy.default_cap`），
    也作挂到 `WorkLease` 上的 granted cap。
    """

    calls: int | None = None
    tokens: int | None = None
    cost: float | None = None
    wall_seconds: int | None = None

    def exceeded_by(self, totals: "UsageTotals") -> bool:
        """`totals` 是否在任一轴上达到或超过本 cap。

        tokens 轴按 input+output 求和。某轴为 `None` 则不校验。
        费用轴：当 `totals.cost is None`（费用未知）时跳过——未知费用保持未知
        （不折成 0），既不据此放行、也不据此拦截一次费用越线。"""

        if self.calls is not None and totals.calls >= self.calls:
            return True
        if self.tokens is not None and (
            totals.input_tokens + totals.output_tokens
        ) >= self.tokens:
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
    """broker 的带版本、偏保守默认值。

    所有阈值都是 policy（改动时人手 bump `policy_version`）。v0 刻意保守：
    default cap 小、额度阈值 80%、额度敏感的 default 默认「先问人」。
    等真观察到 default 态任务质量后再放开。

    预算 vs 并发：每日 cap 在默认 `concurrency_per_account=1` 下严格生效；
    并发更高时 cap 退化为软上限（同一账号两个在途 default grant 都能在记账前通过余额检查）。
    完整的 grant 时预算预留属于后续 scheduler 接入的工作。
    """

    policy_version: str = "workbroker-v0"
    #: 唯一的内部预算 cap，只对 default 生效；foreground / maintenance 拿无限额度。
    default_cap: BudgetDimensions = field(
        default_factory=lambda: BudgetDimensions(calls=100)
    )
    #: 任一受监控的模型调用窗口已用% ≥ 该值时，default defer。
    default_quota_used_threshold: float = 80.0
    #: 为 True 时，额度不足的 default 在 denial 上带 `ask_human=True`。
    #: v0 占位：M8 的通用自主度拨杆还没建，在那之前这是唯一开关。
    default_ask_human_on_quota_sensitive: bool = True
    #: default 补跑 tick 超过该秒数即丢弃。
    default_tick_max_lag_seconds: int = 3600
    #: 每个 (provider, account) 允许的并发 work lease 数。
    concurrency_per_account: int = 1
    #: work lease 的 TTL。持有者停止心跳 / 崩溃超过该值即丢槽；
    #: fenced 写也会立即拒绝过期 lease。
    lease_ttl_seconds: int = 600
    #: 每个 provider 的 failover 顺序。请求没指定 `account_id` 时按此走；
    #: 指定了 `account_id` 则不 failover。
    glm_account_order: tuple[str, ...] = ("glm-a", "glm-b")
    codex_account_order: tuple[str, ...] = ("codex",)

    def replace(self, **overrides: Any) -> "BrokerPolicy":
        """返回替换了部分字段的副本（frozen dataclass 辅助方法）。"""

        return replace(self, **overrides)

    def account_order(self, provider: Provider) -> tuple[str, ...]:
        """某 provider 的 failover 顺序。"""

        if provider is Provider.GLM:
            return self.glm_account_order
        return self.codex_account_order

    def __post_init__(self) -> None:
        """校验会被坏 policy 静默破坏的不变量。"""

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
    """请求一份模型资源 lease 的工作单元。

    Attributes:
        kind: foreground / default / maintenance。
        provider: 这份工作从哪个 provider 抽额度。
        model_tier: 估计的 fast / deep（真正选档由 router 之后决定）。
        account_id: 偏好账号；`None` 让 broker failover。
        task_id / work_item_id: 归因指针（仅定位，不参与仲裁）。
        priority: 仅供参考，**预留**给 scheduler 的待办队列排序——v0 broker
            同步仲裁每个请求（没有待办队列），所以 priority 目前无效。
        deadline: 仅供参考，**预留**——v0 broker 不强制 deadline。
        catchup: NONE / MAINTENANCE_MERGE / DEFAULT_DROP（会和 kind 交叉校验）。
        catchup_scope / catchup_period: 标识一次 maintenance 补跑
            （如 scope="review", period="2026-07-22"）；MERGE 时必填。
        scheduled_for: 这份工作本该触发的时刻（aware ISO），驱动 default 滞后丢弃。
        budget_cap: 对 policy cap 的**收窄**（仅 default），只能更紧、不能更松。
        idempotency_key: scheduler 对**一次**获取的重试身份，跨所有槽生效（请求级，非槽级）。
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
        """交叉校验 catchup 与 kind、必填字段，并校验容易传错的时间戳 / cap。"""

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
                raise ValueError(
                    "DEFAULT_DROP catchup is only legal for default work"
                )
            # 没有触发时刻就没法判「滞后」，gate 会把漏掉的 tick 当实时任务跑。
            if not self.scheduled_for:
                raise ValueError(
                    "DEFAULT_DROP requires a non-empty scheduled_for"
                )
        if self.scheduled_for is not None:
            WorkBroker._parse_iso(self.scheduled_for)  # raises on garbage/naive
        if self.deadline is not None:
            WorkBroker._parse_iso(self.deadline)
        if self.budget_cap is not None:
            WorkBroker._validate_cap(self.budget_cap)

    @property
    def fingerprint(self) -> str:
        """本请求形状的稳定身份，绑定到 idempotency key，使「同 key 不同请求」被拒绝
        （而不是静默复用上次的 lease）。broker 不仲裁的量（priority / scheduled_for /
        deadline / budget_cap / model_tier）故意不纳入。"""

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
    """一个并发槽上已授予的 lease。

    `granted_cap` 为 `None` 表示无限额度（foreground / maintenance）；
    default 则是（收窄后的）cap，runner 应遵守。后续每次 fenced 写都要带 `fencing_token`；
    `expires_at` 已过的 lease 在写时被拒。
    """

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
    """未授予的请求，带可区分的原因。

    `retry_after_seconds` 是提示（最早忙槽过期时刻）。`ask_human` 标记额度敏感的
    default 应该提示人而不是静默 defer（自主度拨杆）。`failed_account_id` + `detail`
    列出哪些候选账号、分别为何卡住——混合失败不被一句笼统的话盖过。
    """

    reason: DenialReason
    detail: str
    retry_after_seconds: float | None = None
    ask_human: bool = False
    failed_account_id: str | None = None


@dataclass(frozen=True)
class UsageRecord:
    """一次实际用量观测。各归因维度从 LEASE 派生——持有者伪造不了
    provider/account/kind/task；只有量、时间戳、可选 observation id 是调用方给的。

    Attributes:
        calls / input_tokens / output_tokens: 非负计数。
        cost: 可见费用，未知则 `None`（保持未知）。
        wall_seconds: 该调用消耗的墙钟，或 `None`。
        occurred_at: aware ISO 时间戳（必填、会校验）。
        observation_id: 可选的单次观测幂等键；同 `(lease_id, observation_id)` 重复插入是空操作
            （重试不会重复记账）。
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    wall_seconds: int | None = None
    occurred_at: str = ""
    observation_id: str | None = None


@dataclass(frozen=True)
class UsageTotals:
    """按某过滤条件聚合的用量（provider / account / kind / day / task）。

    `cost` 在范围内任一行费用未知时为 `None`——「未知费用保持未知」，绝不折成 0。
    """

    calls: int
    input_tokens: int
    output_tokens: int
    cost: float | None
    wall_seconds: int


# ---------------------------------------------------------------------- schema

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS work_leases (
    lease_id TEXT PRIMARY KEY,
    slot TEXT NOT NULL,
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    work_kind TEXT NOT NULL,
    model_tier TEXT NOT NULL,
    task_id TEXT,
    work_item_id TEXT,
    -- JSON BudgetDimensions；NULL 表示无限额度（foreground / maintenance）。
    -- 读回时解析成 WorkLease.granted_cap。
    granted_cap TEXT,
    -- holding(0) vs running(1)：由 begin_call 在发起 native 调用前置 1，
    -- 这样已发出调用的 default 不再被抢占；从没 begin_call 的 lease 保持 holding、可被抢占。
    started INTEGER NOT NULL DEFAULT 0,
    -- maintenance 不可中断的写窗口（begin_critical_section）。
    in_critical INTEGER NOT NULL DEFAULT 0,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    -- 仅审计用（记录是哪个请求 key 拿到的）；去重权威是 work_idempotency_keys（请求级，非槽级）。
    idempotency_key TEXT,
    policy_version TEXT NOT NULL,
    released_at TEXT
);

-- CAS 原语：每个槽至多一个活跃（未释放）持有者。对应 model_os.store 的 idx_leases_active。
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_leases_active
    ON work_leases(slot) WHERE released_at IS NULL;

-- 每槽严格单调的 fencing 计数器。对应 model_os.store 的 lease_fence_counters。
CREATE TABLE IF NOT EXISTS work_fence_counters (
    slot TEXT PRIMARY KEY,
    last_token INTEGER NOT NULL
);

-- 请求级幂等：一个 key -> 一个跨所有槽的活跃 lease。
-- 槽是 broker 动态选的，按槽去重会让同一个请求在两个槽各拿一份；这里在请求级去重。
-- fingerprint 把 key 绑到请求形状（kind/provider/account/task/catchup），
-- 使「同 key 不同请求」被拒绝，而不是静默复用上次的 lease。
CREATE TABLE IF NOT EXISTS work_idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_usage (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT,
    lease_id TEXT,
    -- 归因维度在记账时从 LEASE 行拷贝，不信调用方（持有 GLM foreground 的不能记成 Codex 用量）。
    provider TEXT NOT NULL,
    account_id TEXT NOT NULL,
    work_kind TEXT NOT NULL,
    model_tier TEXT NOT NULL,
    task_id TEXT,
    work_item_id TEXT,
    calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost REAL,
    wall_seconds INTEGER,
    occurred_at TEXT NOT NULL,
    day TEXT NOT NULL,
    policy_version TEXT NOT NULL
);

-- 单次观测幂等：同 `(lease_id, observation_id)` 的重复 record_usage 是空操作
-- （重试不重复记账）。作用域到 lease，使两个不同 lease 可复用同一 observation id。
CREATE UNIQUE INDEX IF NOT EXISTS idx_work_usage_obs
    ON work_usage(lease_id, observation_id) WHERE observation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_work_usage_dim
    ON work_usage(work_kind, provider, account_id, day);

-- maintenance 补跑 claim：`(scope, period)` 在 grant 时 CLAIMED（靠主键原子去重），
-- 只有成功 `complete` 才 COMPLETED。lease 已死（完成前崩溃）的 claim 会自愈——
-- 下次请求清掉它重新 grant，必要维护不会永久丢失。
CREATE TABLE IF NOT EXISTS work_catchup_watermark (
    scope TEXT NOT NULL,
    period TEXT NOT NULL,
    work_kind TEXT NOT NULL,
    lease_id TEXT,
    state TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (scope, period, work_kind)
);
"""


def _default_now() -> datetime:
    """真实 UTC 时钟（测试注入可控时钟）。"""

    return datetime.now(timezone.utc)


# --------------------------------------------------------------------- broker


class WorkBroker:
    """在 foreground / default / maintenance 之间仲裁模型资源 lease。

    自带 SQLite 连接（WAL + IMMEDIATE 事务 + 线程安全）和自己的表。可选地查询
    `QuotaReadModel` 获取外部 provider 额度；没有 read model 时额度闸门跳过
    （绝不在没有数据时凭空拦截）。

    线程安全：每个 public 方法都取 `self._lock`（RLock），所以仲裁——包括
    check-then-acquire-then-preempt 整段——在同一 broker 的多线程间串行。
    SQLite 的 `in_transaction` 是连接级、非线程级；正是 RLock 让多步命令原子化
    （这点和 model_os.store 踩过的并发坑一致）。`open` / `close` 也加锁，
    避免并发请求拿到正在关闭 / 还没开的连接。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        policy: BrokerPolicy | None = None,
        read_model: QuotaReadModel | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        """记住配置；调 `open()` 才真正连库。"""

        self._path = Path(db_path)
        self._policy = policy or BrokerPolicy()
        self._read_model = read_model
        self._now = now_fn or _default_now
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        # 运行时限流 cooldown：(provider, account_id) -> 过期 epoch 秒。
        # 只放内存：限流是 provider 的瞬态运行时事实，重启后下次调用会重新学到。
        self._cooldowns: dict[tuple[Provider, str], float] = {}

    # --------------------------------------------------- lifecycle / bootstrap

    @property
    def policy(self) -> BrokerPolicy:
        """当前 policy（只读视图）。"""

        return self._policy

    def open(self) -> None:
        """打开连接，库不存在则建表。"""

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
        """open 后扫一次上次崩溃留下的过期 lease，返回回收数。

        扫描把「过期但未释放」的行标 released，槽在重启后立即可用。
        这里不 bump fence 计数（还没有新持有者）——下次 acquire 会 mint 更高的 token，
        被扫掉的 lease_id 也不再活跃，旧 token 无论如何都失效。
        """

        self.open()
        return self.recover()

    def close(self) -> None:
        """关闭连接（幂等）。"""

        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _create_connection(self) -> sqlite3.Connection:
        """WAL 连接 + 线程安全标志（与 model_os.store 一致）。"""

        conn = sqlite3.connect(str(self._path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = "IMMEDIATE"
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[None]:
        """IMMEDIATE 事务。同线程内经 in_transaction 判定可重入。"""

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

    # --------------------------------------------------------------- public API

    def request(self, req: WorkRequest) -> WorkLease | WorkDenial:
        """仲裁一次请求：授予 lease 或返回 denial。

        整个请求——幂等 reclaim、catch-up gate（可能清死 claim）、仲裁——跑在
        **同一个会提交的事务**里。把 gate 清死 claim 的 DELETE 拆到事务外，
        会留在一个隐式自动开启的事务里，后续仲裁的 `_tx` 会误判成嵌套调用而不 COMMIT——
        授予的 lease 就成了幽灵：只在本连接可见，close 时被回滚。外层这一个事务是 lease 持久化的保证。"""

        now = self._now()
        with self._lock, self._tx():
            # 幂等优先：真正的重试在 catch-up gate 把它变成 CATCHUP_ALREADY_DONE 之前，
            # 先返回它那份活跃 lease。
            reclaimed = self._reclaim_idempotent(req, now)
            if reclaimed is not None:
                return reclaimed
            gate = self._catchup_gate(req, now)
            if gate is not None:
                return gate
            return self._arbitrate_body(req, now)

    def begin_call(self, lease_id: str, fencing_token: int) -> None:
        """把 lease 标记为 STARTED——发起 native 模型调用**之前**调它。

        把 `started` 置 1，使已发出调用的 default 不再被 foreground 抢占
        （否则抢占窗口会横跨整个 native 调用，因为用量要等调用返回后才知道）。
        token 不对或 lease 已过期时抛 `StaleWorkLease`——过期持有者不能再发新调用。"""

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
        """记一笔用量观测，各归因维度从 LEASE 派生。

        量强制非负；重复的 `(lease_id, observation_id)` 是空操作。token 不是当前
        活跃持有者的、或 lease 已过期时抛 `StaleWorkLease`。返回本 lease 的
        (kind, provider, account) 当日累计，让 runner 知道 default 离 cap 还有多远。
        """

        self._validate_usage(usage)
        with self._lock, self._tx():
            assert self._conn is not None
            row = self._resolve_active_for_write(lease_id, fencing_token)
            # 单次观测幂等：同 (lease_id, observation_id) 的重试是空操作
            # （作用域到 lease，两个不同 lease 可复用同一 observation id）。
            if usage.observation_id is not None:
                seen = self._conn.execute(
                    "SELECT 1 FROM work_usage WHERE lease_id=? AND observation_id=?",
                    (lease_id, usage.observation_id),
                ).fetchone()
                if seen is not None:
                    day = self._utc_day(usage.occurred_at)
                    return self._totals_in_tx(
                        work_kind=WorkKind(row["work_kind"]),
                        provider=Provider(row["provider"]),
                        account_id=row["account_id"],
                        day=day,
                    )
            self._conn.execute(
                "UPDATE work_leases SET started=1 WHERE lease_id=?", (lease_id,)
            )
            day = self._utc_day(usage.occurred_at)
            self._conn.execute(
                "INSERT INTO work_usage (observation_id, lease_id, provider, "
                "account_id, work_kind, model_tier, task_id, work_item_id, calls, "
                "input_tokens, output_tokens, cost, wall_seconds, occurred_at, "
                "day, policy_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    usage.observation_id,
                    lease_id,
                    row["provider"],
                    row["account_id"],
                    row["work_kind"],
                    row["model_tier"],
                    row["task_id"],
                    row["work_item_id"],
                    usage.calls,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cost,
                    usage.wall_seconds,
                    usage.occurred_at,
                    day,
                    self._policy.policy_version,
                ),
            )
            return self._totals_in_tx(
                work_kind=WorkKind(row["work_kind"]),
                provider=Provider(row["provider"]),
                account_id=row["account_id"],
                day=day,
            )

    def begin_critical_section(self, lease_id: str, fencing_token: int) -> None:
        """标记一个 MAINTENANCE lease 的不可中断写窗口。

        只有 maintenance 能进临界区——default 把自己标 critical 会瓦解 foreground 抢占，
        所以拒绝。token 不对或 lease 已过期时抛 `StaleWorkLease`。

        v0 说明：maintenance 本就不可抢占，这个标志目前不承重——它是预留 hook，
        给将来加 maintenance 抢占路径的版本用（届时只保护临界窗口）。保留它是为了
        API 先存在、把「仅 maintenance」这个不变量从第一天就强制住。"""

        with self._lock, self._tx():
            assert self._conn is not None
            row = self._resolve_active_for_write(lease_id, fencing_token)
            if row["work_kind"] != WorkKind.MAINTENANCE.value:
                raise ValueError(
                    "only maintenance work may enter a critical section"
                )
            self._conn.execute(
                "UPDATE work_leases SET in_critical=1 WHERE lease_id=?", (lease_id,)
            )

    def renew(
        self, lease_id: str, fencing_token: int, *, ttl_seconds: int | None = None
    ) -> WorkLease:
        """延长 lease 的过期时刻，避免长 native 调用丢槽。

        native 模型调用在途、可能超过 TTL 时调它。token 不对或 lease 已过期时抛
        `StaleWorkLease`（过期后无法复活一个已被接管的槽）。返回续约后的 lease
        （同 id + token，更晚的 `expires_at`）。
        """

        ttl = ttl_seconds if ttl_seconds is not None else self._policy.lease_ttl_seconds
        if ttl <= 0:
            raise ValueError("renew ttl_seconds must be positive")
        with self._lock, self._tx():
            assert self._conn is not None
            row = self._resolve_active_for_write(lease_id, fencing_token)
            now = self._now()
            expires_iso = (now + timedelta(seconds=ttl)).isoformat()
            self._conn.execute(
                "UPDATE work_leases SET expires_at=? WHERE lease_id=?", (expires_iso, lease_id)
            )
            return replace(self._row_to_lease(row), expires_at=expires_iso)

    def complete(self, lease_id: str, fencing_token: int) -> bool:
        """把一个 maintenance 补跑 lease 标记为成功：catch-up watermark 推进到 COMPLETED，再释放槽。

        与 `release` 区分：`release` 是放弃、**不**推进 catch-up（失败/放弃的 maintenance
        留 claim 为 claimed，下次请求清死 claim 重跑必要工作）。lease 活跃且完成返回 True，
        已释放/未知返回 False。token 不匹配抛 `StaleWorkLease`。"""

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
        """释放（放弃）一个 lease，腾出槽。

        活跃且释放了返回 True，已释放/未知返回 False。token 不匹配才抛 `StaleWorkLease`
        （旧持有者想释放别人的槽）。释放一个已过期的 lease 是无害清理（仍标 released）。
        **不**推进 maintenance catch-up——成功跑完用 `complete`；放弃的 catch-up 留 claim 自愈。"""

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
        """记录一次 provider 运行时限流（429 / cooldown）。"""

        expiry = self._now().timestamp() + cooldown_seconds
        with self._lock:
            self._cooldowns[(provider, account_id)] = expiry

    def active_leases(self) -> tuple[WorkLease, ...]:
        """当前活跃（未释放、未过期）的 work lease 快照，按获取时间升序。"""

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
        """按给定条件聚合用量（归因 / 预算检查用）。"""

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
        """扫一次过期但未释放的 lease（重启时调），返回回收数。"""

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

    # ----------------------------------------------------------- arbitration

    def _arbitrate_body(
        self, req: WorkRequest, now: datetime
    ) -> WorkLease | WorkDenial:
        """账号循环 + 闸门 + 抢占。跑在 `request` 的事务里——到达这里之前，
        幂等 reclaim 和 catch-up gate 已经（在同一事务里）跑过了。"""

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
                    busy_expiry if earliest_expiry is None else min(earliest_expiry, busy_expiry)
                )

        # foreground 可抢占未开始（没 begin_call）的 default。
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
        """grant 后的记账：请求级幂等映射 + catch-up claim。跑在仲裁事务里，
        使 lease 与其 claim 原子落库。返回 lease；若本请求在跨进程 catch-up claim
        竞争中输了，则先把刚拿到的 lease 释放、返回 CATCHUP_ALREADY_DONE。"""

        assert self._conn is not None
        if req.catchup is CatchupPolicy.MAINTENANCE_MERGE:
            # 主键是原子去重。ON CONFLICT DO NOTHING + rowcount 告诉我们有没有赢得
            # (scope, period) 的 claim；放在幂等映射之前，输了竞争时撤销更干净。
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
                # 输给了另一个连接——撤销本次 grant、返回合并。不抛异常：
                # 事务会把这次释放干净提交（在这里抛反而会回滚它）。
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
        """要尝试的账号。指定了 `account_id` 则不 failover；否则按 policy 顺序。
        foreground / maintenance 优先健康账号（它们不被低额度阻断，但健康账号空闲时
        不该堆到快满的账号上）；default 保持 policy 顺序——它的循环本就跳过额度低的账号、
        回落到健康账号。"""

        if req.account_id is not None:
            return (req.account_id,)
        base = self._policy.account_order(req.provider)
        if req.kind is WorkKind.DEFAULT or self._read_model is None:
            return base
        # 三态排序：已知健康 -> 未知（无/过期快照）-> 已知低。
        # 二态（健康/低）排序会把未知并入健康，靠稳定排序的先后，反而把无数据账号排在已知健康前面。
        # foreground / maintenance 不被低额度阻断，但健康账号空闲时不该堆到快满的账号上。
        order = {"healthy": 0, "unknown": 1, "low": 2}
        return tuple(
            sorted(
                base, key=lambda acc: order[self._quota_state(req.provider, acc)]
            )
        )

    def _catchup_gate(self, req: WorkRequest, now: datetime) -> WorkDenial | None:
        """仲裁前套用 catch-up 策略。"""

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
            assert req.catchup_scope and req.catchup_period  # WorkRequest 里已校验
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
        """尝试某账号的所有槽号；返回 lease，或最早忙槽过期时刻（epoch 秒）供调用方算 retry-after。"""

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
        """对一个槽做 CAS 获取，复刻 `model_os.store.acquire_lease` / `_takeover_or_conflict`。

        - 过期持有者标 released、槽被接管。
        - 活跃持有者 -> 返回 `None`（忙），调用方试下一个槽。
        每次 grant 为该槽 mint 一个严格更高的 fencing token。这里只做槽级 CAS——
        请求级幂等由 `_reclaim_idempotent` 处理。
        """

        assert self._conn is not None
        now_iso = now.isoformat()
        expires_iso = (now + timedelta(seconds=self._policy.lease_ttl_seconds)).isoformat()
        holder = self._conn.execute(
            "SELECT * FROM work_leases WHERE slot=? AND released_at IS NULL",
            (slot,),
        ).fetchone()
        if holder is not None:
            if holder["expires_at"] > now_iso:
                return None  # 确实忙
            # 过期 -> 释放该槽（接管前奏，此时还没有新持有者）
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
        """候选账号上最老的、未开始、非临界的 default lease——foreground 可抢占的 victim。
        「未开始」指没调过 `begin_call`，即还没发出 native 调用。"""

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
        """把 lease 标 released（抢占 / 竞争输了）；调用方已持锁。"""

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
        """从各账号结果里挑出 denial 原因。

        优先级：QUOTA_LOW > RATE_LIMIT > BUDGET_EXHAUSTED > SLOT_BUSY。
        detail 列出每个账号的结果，混合失败不被一句笼统话盖过；
        failed_account_id 是命中该优先级的第一个账号。"""

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

    # ------------------------------------------------------------- gates

    def _in_cooldown(self, provider: Provider, account: str, now: datetime) -> bool:
        """True if a runtime rate-limit cooldown is still active. Caller holds
        the lock; ``report_rate_limit`` (the only writer) also takes it."""

        expiry = self._cooldowns.get((provider, account))
        if expiry is None:
            return False
        if now.timestamp() >= expiry:
            self._cooldowns.pop((provider, account), None)  # expired — drop it
            return False
        return True

    def _quota_low(self, provider: Provider, account: str) -> bool:
        """外部模型调用额度已用% 是否达到/超过阈值。

        无 read model、无快照、或快照非 OK（stale/error/no-data）时返回 False——
        provider 运行时限流才是权威，不是可能过期的快照。"""

        return self._quota_state(provider, account) == "low"

    def _quota_state(self, provider: Provider, account: str) -> str:
        """failover 排序用的三态额度健康度。

        - `healthy`：OK 快照，所有模型调用窗口都在阈值以下。
        - `low`：OK 快照，有模型调用窗口达到/超过阈值。
        - `unknown`：无 read model、无快照、或非 OK（stale/error/no-data）快照——
          与 healthy 区分，使无数据账号不会被排到已知健康账号前面。
        """

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
        """今日 (provider, account) 的 default 用量是否到 cap。cap 只能被请求收窄、不能放宽。"""

        cap = self._narrow_cap(self._policy.default_cap, req.budget_cap)
        totals = self._totals_in_tx(
            work_kind=WorkKind.DEFAULT,
            provider=req.provider,
            account_id=account,
            day=now.date().isoformat(),
        )
        return cap.exceeded_by(totals)

    # ------------------------------------------------------------- helpers

    def _resolve_active_for_write(
        self, lease_id: str, fencing_token: int
    ) -> sqlite3.Row:
        """为一次 fenced 写加载 lease，拒绝过期/失效的持有者。

        lease 已释放/未知、token 不对、或 lease 已过期（TTL 到了）都抛 `StaleWorkLease`——
        过期持有者不能记账、不能标临界区。"""

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
        """请求级幂等。返回重试 key 对应的活跃 lease，或 None 让后续走全新 grant。
        上次的 grant 若已过期/已释放，先接管/清掉。key 被复用在**另一个**请求上
        （fingerprint 不符）则抛 `IdempotencyConflict`。"""

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
            # 上次 grant 已释放 -> key 用尽 -> 清掉、全新 grant
            self._conn.execute(
                "DELETE FROM work_idempotency_keys WHERE idempotency_key=?",
                (req.idempotency_key,),
            )
            return None
        if row["expires_at"] <= now_iso:
            # 过期 -> 接管：标旧的 released、清映射、全新 grant
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
        """返回某槽的下一个 fencing token，并自增计数器。"""

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
        """某槽上活跃持有者最近的过期时刻（epoch 秒）。"""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT expires_at FROM work_leases WHERE slot=? AND released_at IS NULL "
            "AND expires_at > ?",
            (slot, now.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["expires_at"]).timestamp()

    def _retry_after(self, earliest_expiry: float | None, now: datetime) -> float | None:
        """距最近一个忙槽释放的秒数；下限取一个小值。"""

        if earliest_expiry is None:
            return None
        return max(0.1, earliest_expiry - now.timestamp())

    def _catchup_seen(
        self, scope: str, period: str, kind: WorkKind, now: datetime
    ) -> bool:
        """某 maintenance `(scope, period)` 补跑是否已完成或在途。

        COMPLETED 的行算完成。CLAIMED 但 lease 仍活跃的算在途（另一个 runner 在跑）。
        CLAIMED 但 lease 已死（完成前崩溃）的会自愈：在这里清掉、让调用方重新 grant——
        必要维护不会永久丢失。"""

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
        # claimed —— 它的 lease 还活着吗？
        lease = self._conn.execute(
            "SELECT 1 FROM work_leases WHERE lease_id=? AND released_at IS NULL "
            "AND expires_at > ?",
            (row["lease_id"], now.isoformat()),
        ).fetchone()
        if lease is not None:
            return True
        # 死 claim（崩溃留下的）-> 清掉、让调用方重新 grant
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
        """按条件 SUM 用量。范围内任一行费用未知时 `cost` 为 None（绝不折成 0）。
        调用方已持锁 + 已开事务。"""

        assert self._conn is not None
        clauses: list[str] = []
        params: list[Any] = []
        if day is not None:
            clauses.append("day=?")
            params.append(day)
        if work_kind is not None:
            clauses.append("work_kind=?")
            params.append(work_kind.value)
        if provider is not None:
            clauses.append("provider=?")
            params.append(provider.value)
        if account_id is not None:
            clauses.append("account_id=?")
            params.append(account_id)
        if task_id is not None:
            clauses.append("task_id=?")
            params.append(task_id)
        if model_tier is not None:
            clauses.append("model_tier=?")
            params.append(model_tier.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        # cost：无行 -> 0；所有行费用已知 -> SUM；存在未知 -> NULL
        row = self._conn.execute(
            "SELECT COALESCE(SUM(calls),0) AS calls, "
            "COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, "
            "CASE WHEN COUNT(*)=0 THEN 0 "
            "WHEN COUNT(cost)=COUNT(*) THEN SUM(cost) ELSE NULL END AS cost, "
            "COALESCE(SUM(wall_seconds),0) AS wall_seconds "
            f"FROM work_usage{where}",
            params,
        ).fetchone()
        cost_raw = row["cost"]
        return UsageTotals(
            calls=int(row["calls"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cost=float(cost_raw) if cost_raw is not None else None,
            wall_seconds=int(row["wall_seconds"]),
        )

    def _decide_granted_cap(self, req: WorkRequest) -> BudgetDimensions | None:
        """挂到 lease 上的 cap。只有 DEFAULT 受内部 cap 约束；请求只能收窄 policy cap、
        不能放宽。foreground / maintenance 一律拿无限额度。"""

        if req.kind is WorkKind.DEFAULT:
            return self._narrow_cap(self._policy.default_cap, req.budget_cap)
        return None

    @staticmethod
    def _narrow_cap(
        policy_cap: BudgetDimensions, req_cap: BudgetDimensions | None
    ) -> BudgetDimensions:
        """有效 cap：每轴取 policy 与请求中更紧的那个（None 表示该轴不限 = 求 min 时视作无穷）。
        请求只能让 cap 更小、不能更大（不允许模型自行加额）。"""

        r = req_cap

        def int_axis(p: int | None, rv: int | None) -> int | None:
            if p is None and rv is None:
                return None
            if p is None:
                return rv
            if rv is None:
                return p
            return min(p, rv)

        def float_axis(p: float | None, rv: float | None) -> float | None:
            if p is None and rv is None:
                return None
            if p is None:
                return rv
            if rv is None:
                return p
            return min(p, rv)

        return BudgetDimensions(
            calls=int_axis(policy_cap.calls, r.calls if r else None),
            tokens=int_axis(policy_cap.tokens, r.tokens if r else None),
            cost=float_axis(policy_cap.cost, r.cost if r else None),
            wall_seconds=int_axis(policy_cap.wall_seconds, r.wall_seconds if r else None),
        )

    @staticmethod
    def _slot_id(provider: Provider, account: str, idx: int) -> str:
        """稳定的槽标识：`{provider}:{account}:{index}`。"""

        return f"{provider.value}:{account}:{idx}"

    @staticmethod
    def _cap_to_json(cap: BudgetDimensions | None) -> str | None:
        """序列化 granted cap；`None`（无限额度）存 NULL。"""

        if cap is None:
            return None
        return json.dumps(
            {
                "calls": cap.calls,
                "tokens": cap.tokens,
                "cost": cap.cost,
                "wall_seconds": cap.wall_seconds,
            }
        )

    def _row_to_lease(self, row: sqlite3.Row) -> WorkLease:
        """从一行 work_leases 重建 WorkLease。"""

        cap_json = row["granted_cap"]
        granted_cap = None
        if cap_json:
            data = json.loads(cap_json)
            granted_cap = BudgetDimensions(
                calls=data.get("calls"),
                tokens=data.get("tokens"),
                cost=data.get("cost"),
                wall_seconds=data.get("wall_seconds"),
            )
        return WorkLease(
            lease_id=row["lease_id"],
            slot=row["slot"],
            provider=Provider(row["provider"]),
            account_id=row["account_id"],
            work_kind=WorkKind(row["work_kind"]),
            model_tier=ModelTier(row["model_tier"]),
            granted_cap=granted_cap,
            acquired_at=row["acquired_at"],
            expires_at=row["expires_at"],
            fencing_token=int(row["fencing_token"]),
            task_id=row["task_id"],
            work_item_id=row["work_item_id"],
        )

    # ------------------------------------------------------------- validation

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        """严格解析带时区的 ISO 时间戳；naive / 非法输入抛错。"""

        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"not an ISO timestamp: {value!r}") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"timestamp lacks timezone offset: {value!r}")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _utc_day(occurred_at: str) -> str:
        """ISO 时间戳对应的 UTC `YYYY-MM-DD` 分桶（每日 cap 的 key）。"""

        return WorkBroker._parse_iso(occurred_at).date().isoformat()

    @staticmethod
    def _validate_cap(cap: BudgetDimensions) -> None:
        """对畸形 cap 快速失败。整数轴必须是真 int（非 bool/float）且非负；
        cost 必须有限且非负。挡住 NaN/Infinity——``< 0`` 对 NaN 恒 False 会放行。"""

        for label, val in (
            ("calls", cap.calls),
            ("tokens", cap.tokens),
            ("wall_seconds", cap.wall_seconds),
        ):
            if val is None:
                continue
            # bool 是 int 的子类——显式拒掉
            if isinstance(val, bool) or not isinstance(val, int):
                raise ValueError(f"BudgetDimensions.{label} must be an int, got {val!r}")
            if val < 0:
                raise ValueError(f"BudgetDimensions.{label} must be non-negative")
        if cap.cost is not None and (
            isinstance(cap.cost, bool)
            or not isinstance(cap.cost, int | float)
            or not math.isfinite(cap.cost)
            or cap.cost < 0
        ):
            raise ValueError("BudgetDimensions.cost must be a finite, non-negative number")

    @staticmethod
    def _validate_usage(usage: UsageRecord) -> None:
        """对畸形用量观测快速失败（边界校验）。"""

        if not usage.occurred_at:
            raise ValueError(
                "UsageRecord.occurred_at must be a non-empty ISO timestamp"
            )
        WorkBroker._parse_iso(usage.occurred_at)  # 非法/naive 会抛
        for label, val in (
            ("calls", usage.calls),
            ("input_tokens", usage.input_tokens),
            ("output_tokens", usage.output_tokens),
        ):
            if val < 0:
                raise ValueError(f"UsageRecord.{label} must be non-negative")
        if usage.wall_seconds is not None and usage.wall_seconds < 0:
            raise ValueError("UsageRecord.wall_seconds must be non-negative")
        if usage.cost is not None and (not math.isfinite(usage.cost) or usage.cost < 0):
            raise ValueError("UsageRecord.cost must be finite and non-negative")
