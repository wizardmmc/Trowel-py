"""Model OS 的事务式 Store。

Store 使用独立 SQLite WAL。结构化命令在 ``RLock`` 内通过 ``_tx`` 显式开启
``BEGIN IMMEDIATE``，使状态回放、门禁、journal 和 live table 更新保持原子；
``_read_tx`` 则保证派生状态与 live lease/foreground 读取来自同一快照。

``event_id``、``decision_id`` 和可控命令的 ``idempotency_key`` 提供幂等身份；
lease fencing 在持久化层拒绝陈旧写入。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from contextlib import contextmanager
from dataclasses import replace

from trowel_py.model_os.episode_snapshot_codec import (
    pending_from_payload as _run_pending_from_payload,
)
from trowel_py.model_os.episode_snapshot_codec import (
    pending_to_payload as _run_pending_to_payload,
)
from trowel_py.model_os.episode_snapshot_codec import (
    snapshot_from_payload as _run_snapshot_from_payload,
)
from trowel_py.model_os.episode_snapshot_codec import (
    snapshot_to_payload as _run_snapshot_to_payload,
)
from trowel_py.model_os.episode_snapshot_codec import (
    validate_snapshot as _run_validate_snapshot,
)
from trowel_py.model_os.episode_recovery import (
    build_recovery_partial as _run_build_recovery_partial,
)
from trowel_py.model_os.redaction import redact_payload
from trowel_py.model_os.reducer import (
    EpisodeState,
    Snapshot,
    TaskState,
    initial_snapshot,
    reduce_event,
)
from trowel_py.model_os.store_journal_codec import (
    decision_from_row as _run_decision_from_row,
)
from trowel_py.model_os.store_journal_codec import (
    decision_params as _run_decision_params,
)
from trowel_py.model_os.store_journal_codec import dumps as _run_dumps
from trowel_py.model_os.store_journal_codec import (
    event_from_row as _run_event_from_row,
)
from trowel_py.model_os.store_journal_codec import (
    event_identity as _run_event_identity,
)
from trowel_py.model_os.store_journal_codec import (
    event_params as _run_event_params,
)
from trowel_py.model_os.store_journal_codec import (
    event_row_identity as _run_event_row_identity,
)
from trowel_py.model_os.store_journal_codec import (
    lease_from_row as _run_lease_from_row,
)
from trowel_py.model_os.store_journal_codec import (
    payload_json as _run_payload_json,
)
from trowel_py.model_os.store_event_factory import (
    make_episode_event as _run_make_episode_event,
)
from trowel_py.model_os.store_event_factory import (
    make_task_event as _run_make_task_event,
)
from trowel_py.model_os.store_event_factory import (
    make_work_item_event as _run_make_work_item_event,
)
from trowel_py.model_os.store_projection import (
    project_episode_state as _run_project_episode_state,
)
from trowel_py.model_os.store_projection import (
    project_task_state as _run_project_task_state,
)
from trowel_py.model_os.store_schema import SCHEMA_SQL as _SCHEMA_SQL
from trowel_py.model_os.task_commands import TaskCommands
from trowel_py.model_os.types import (
    ArtifactRef,
    DecisionRecord,
    Episode,
    EpisodeSnapshot,
    EpisodeStatus,
    EventEnvelope,
    EventKind,
    Lease,
    MemoryEligibility,
    PendingDescriptor,
    Provenance,
    ReconcileReason,
    SessionPurpose,
    SideEffectRecord,
    SnapshotRef,
    SnapshotSource,
    Task,
    TaskStatus,
    WaitingCondition,
    WaitingSubtype,
    WorkItem,
    WorkItemKind,
    WorkItemStatus,
)
from trowel_py.model_os.context_observer import (
    ContextSample,
    context_sample_to_dict,
)

_SCHEMA_VERSION = 4  # 活跃 lease 才占用幂等 key，释放后允许新 grant。
_DEFAULT_POLICY_VERSION = "v0"

_LOGGER = logging.getLogger(__name__)

# Snapshot 只引用 transcript 而不复制正文；上限避免异常 payload 膨胀 journal。
_MAX_SNAPSHOT_PAYLOAD_BYTES = 256 * 1024

# Task 生命周期事件只能由结构化命令写入，避免调用方绕过状态门禁伪造 Task、
# 复活终态 Task，或在没有 foreground claim 时写入 running。
# ``TASK_CREATION_DENIED`` 只用于审计，reducer 不派生状态，因此保持开放。
_TASK_LIFECYCLE_KINDS = frozenset(
    {
        EventKind.TASK_CREATED,
        EventKind.TASK_STATUS_CHANGED,
        EventKind.TASK_CONSTRAINT_APPENDED,
        EventKind.TASK_WARM_CHANGED,
        EventKind.TASK_WARM_RANK_SET,
        EventKind.TASK_WAITING_SET,
        EventKind.TASK_WAITING_CLEARED,
        EventKind.TASK_AUTHORIZATION_CHANGED,
        EventKind.TASK_COMPLETED,
        EventKind.TASK_CANCELLED,
        EventKind.TASK_ERROR_RECORDED,
        EventKind.FOREGROUND_CLAIMED,
        EventKind.FOREGROUND_RELEASED,
    }
)

# Episode 生命周期事件只能由结构化命令写入，避免调用方伪造 Episode、checkpoint
# 或陈旧 ownership 转移。``LATE_WRITE_REJECTED`` 虽只用于审计，也只能由
# fencing 路径通过 ``_insert_event_in_tx`` 写入。
_EPISODE_LIFECYCLE_KINDS = frozenset(
    {
        EventKind.EPISODE_CREATED,
        EventKind.EPISODE_STATUS_CHANGED,
        EventKind.EPISODE_OWNERSHIP_ACQUIRED,
        EventKind.EPISODE_OWNERSHIP_RELEASED,
        EventKind.EPISODE_YIELD_REQUESTED,
        EventKind.EPISODE_CHECKPOINT_COMMITTED,
        EventKind.EPISODE_CLOSED,
        EventKind.EPISODE_FAILED,
        EventKind.EPISODE_SUSPENDED,
        EventKind.EPISODE_WAIT_RESOLVED,
        EventKind.EPISODE_ACTIVATED,
        EventKind.EPISODE_RECONCILE_REQUIRED,
        EventKind.EPISODE_RECONCILE_RESOLVED,
        EventKind.EPISODE_RECOVERING,
        EventKind.EPISODE_SIDE_EFFECT_RECORDED,
        EventKind.LATE_WRITE_REJECTED,
    }
)

# 持有 ownership lease 时会改变 Episode 权威状态的事件必须携带调用方持有的
# ``(lease_id, owner, fencing_token)``，Store 在持久化前据实时 lease 校验。
# 创建、取得所有权和释放所有权是 lease 生命周期边界，不属于受 fencing 保护的
# 进度写入；``LATE_WRITE_REJECTED`` 只供审计，也不受 fencing 保护。仅把
# ``episode_id`` 用作因果引用的 ``task.*``、``work_item.*`` 事件不进入该集合。
#
# 外部回答、重启时检测通道丢失以及人工或内核的 reconcile 决策都不由 lease
# 持有者推进，因此调用方没有可提交的 lease 三元组。若要求 fencing，恰好在 lease
# 已失效时运行的 ``mark_pending_channel_lost`` 将无法调用。这些事件仍属于
# ``_EPISODE_LIFECYCLE_KINDS``，裸 ``append_event`` 会拒绝，只能由结构化命令写入。
_EPISODE_FENCED_KINDS = frozenset(
    {
        EventKind.EPISODE_STATUS_CHANGED,
        EventKind.EPISODE_YIELD_REQUESTED,
        EventKind.EPISODE_CHECKPOINT_COMMITTED,
        EventKind.EPISODE_CLOSED,
        EventKind.EPISODE_FAILED,
        EventKind.EPISODE_SUSPENDED,
        EventKind.EPISODE_ACTIVATED,
        EventKind.EPISODE_RECOVERING,
        EventKind.EPISODE_SIDE_EFFECT_RECORDED,
    }
)


class LeaseConflict(Exception):
    """CAS lease 抢占败给其他活跃 owner 时抛出。"""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        self.resource_type = resource_type
        self.resource_id = resource_id
        super().__init__(
            f"lease already held: resource_type={resource_type} resource_id={resource_id}"
        )


class ForegroundConflict(Exception):
    """抢占 foreground 败给另一个 Task 时抛出。

    foreground 是没有 TTL 的单行持久化记录，同一时刻只能由一个 Task 持有。
    同一 owner 重试会静默返回，不同 owner 则抛出本异常。
    """

    def __init__(self, current_owner: str | None) -> None:
        self.current_owner = current_owner
        super().__init__(f"foreground already held by task_id={current_owner!r}")


class WarmFull(Exception):
    """提升 Task 会超过 ``warm_limit`` 时抛出。

    warm 是固定容量缓存，溢出时必须显式替换：调用方先把已有 warm Task 降到
    backlog，再提升新 Task。异常携带当前 warm Task ID，供调用方或界面展示选择。
    """

    def __init__(self, limit: int, warm_task_ids: tuple[str, ...]) -> None:
        self.limit = limit
        self.warm_task_ids = warm_task_ids
        super().__init__(
            f"warm pool full (limit={limit}); demote one of {warm_task_ids} first"
        )


class TaskCommandError(Exception):
    """Task 命令违反状态转换、对象存在性或来源权限不变量时抛出。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class EpisodeCommandError(Exception):
    """Episode 命令违反状态、所有权或 checkpoint/snapshot 契约时抛出。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class StaleWriterRejected(Exception):
    """受 fencing 保护的 Episode 写入携带陈旧所有权 token 时抛出。

    写入者的 ``(lease_id, owner, fencing_token)`` 与实时 ownership lease 不符时，
    先拒绝权威状态变更，再记录 ``late_write_rejected`` 审计事件。同一 ``event_id``
    已持久化的幂等重试不抛出；``append_event`` 直接返回原 seq，不再校验 fencing。
    """

    def __init__(
        self,
        episode_id: str,
        reason: str,
        attempted_token: int | None = None,
        current_token: int | None = None,
    ) -> None:
        self.episode_id = episode_id
        self.reason = reason
        self.attempted_token = attempted_token
        self.current_token = current_token
        super().__init__(
            f"stale writer rejected for episode {episode_id!r}: {reason} "
            f"(attempted_token={attempted_token}, current_token={current_token})"
        )


def _now_iso() -> str:
    """返回按字典序可排序的 UTC ISO-8601 时间。"""

    return datetime.now(timezone.utc).isoformat()


def _payload_json(payload: dict[str, Any]) -> tuple[str, str]:
    return _run_payload_json(
        payload,
        redact_fn=redact_payload,
        json_dumps=json.dumps,
        sha256_fn=hashlib.sha256,
        str_type=str,
    )


def _dumps(value: Any) -> str:
    return _run_dumps(
        value,
        redact_fn=redact_payload,
        json_dumps=json.dumps,
        str_type=str,
    )


# INSERT 语句与参数构造器集中在模块级，确保事件、决策及其原子组合使用同一序列化
# 和脱敏规则，避免各方法的副本逐渐分歧。

_EVENT_INSERT_SQL = (
    "INSERT INTO events (event_id, kind, occurred_at, source, provenance, "
    "policy_version, work_item_id, task_id, episode_id, native_session_id, "
    "cause_id, correlation_id, outcome, payload, payload_hash, "
    "lease_id, owner, fencing_token) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_DECISION_INSERT_SQL = (
    "INSERT INTO decisions (decision_id, kind, decided_at, work_item_id, "
    "task_id, episode_id, cause_id, correlation_id, policy_version, "
    "signals, candidates, choice, reason, budget_before, budget_after) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _event_params(event: EventEnvelope, payload_text: str, payload_hash: str) -> tuple:
    return _run_event_params(
        event,
        payload_text,
        payload_hash,
    )


def _event_identity(event: EventEnvelope, payload_hash: str) -> tuple:
    return _run_event_identity(event, payload_hash)


def _event_row_identity(row: sqlite3.Row, payload_hash: str) -> tuple:
    return _run_event_row_identity(row, payload_hash, int_fn=int)


def _decision_params(decision: DecisionRecord) -> tuple:
    return _run_decision_params(
        decision,
        dumps_fn=_dumps,
        redact_fn=redact_payload,
    )


def _lease_from_row(row: sqlite3.Row) -> Lease:
    return _run_lease_from_row(row, lease_type=Lease, int_fn=int)


def _event_from_row(row: sqlite3.Row) -> EventEnvelope:
    return _run_event_from_row(
        row,
        event_type=EventEnvelope,
        provenance_type=Provenance,
        json_loads=json.loads,
        int_fn=int,
    )


def _decision_from_row(row: sqlite3.Row) -> DecisionRecord:
    return _run_decision_from_row(
        row,
        decision_type=DecisionRecord,
        json_loads=json.loads,
    )


def _validate_work_item(kind: WorkItemKind, task_id: str | None) -> None:
    """校验 WorkItem 的结构不变量。

    Task 与 incubation 工作必须引用 Task；default、maintenance 和 experiment
    属于系统工作，不得携带 Task 引用。
    """

    if kind in (WorkItemKind.TASK, WorkItemKind.INCUBATION):
        if not task_id:
            raise ValueError(f"{kind.value} work item requires a task_id (got None)")
    else:
        if task_id is not None:
            raise ValueError(
                f"{kind.value} work item must not reference a task "
                f"(got task_id={task_id!r})"
            )


def _pending_to_payload(p: PendingDescriptor) -> dict[str, Any]:
    return _run_pending_to_payload(p)


def _pending_from_payload(p: dict[str, Any]) -> PendingDescriptor:
    return _run_pending_from_payload(
        p,
        pending_type=PendingDescriptor,
        waiting_subtype=WaitingSubtype,
    )


def _snapshot_to_payload(s: EpisodeSnapshot) -> dict[str, Any]:
    return _run_snapshot_to_payload(
        s,
        encode_pending=_pending_to_payload,
    )


def _validate_episode_snapshot(snapshot: EpisodeSnapshot, payload_text: str) -> None:
    _run_validate_snapshot(
        snapshot,
        payload_text,
        max_payload_bytes=_MAX_SNAPSHOT_PAYLOAD_BYTES,
        error_type=EpisodeCommandError,
    )


def _snapshot_from_payload(p: dict[str, Any]) -> EpisodeSnapshot:
    return _run_snapshot_from_payload(
        p,
        decode_pending=_pending_from_payload,
        snapshot_type=EpisodeSnapshot,
        side_effect_type=SideEffectRecord,
        artifact_type=ArtifactRef,
        snapshot_ref_type=SnapshotRef,
        snapshot_source=SnapshotSource,
    )


class ModelOsStore:
    """为 Model OS journal 提供事务式 SQLite 持久化。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        policy_version: str = _DEFAULT_POLICY_VERSION,
        warm_limit: int = 3,
    ) -> None:
        """保存数据库路径与策略配置；调用 ``open()`` 后才建立连接。

        ``policy_version`` 会写入每条事件和决策，供回放解释策略差异。
        ``warm_limit`` 限制 warm Task 数量，foreground Task 也计入该上限。
        """

        self._path = Path(db_path)
        self._policy_version = policy_version
        self._warm_limit = warm_limit
        self._conn: sqlite3.Connection | None = None
        # SQLite 的事务状态属于连接而非线程；该锁串行化共享连接的命令，避免两个
        # 请求处理器交错进入同一事务。
        self._lock = threading.RLock()
        self._task_commands = TaskCommands(
            self,
            now=lambda: _now_iso(),
            new_id=lambda: uuid4().hex,
            event_type=lambda **kwargs: EventEnvelope(**kwargs),
            task_error=lambda reason: TaskCommandError(reason),
            warm_full=lambda limit, task_ids: WarmFull(limit, task_ids),
            foreground_conflict=lambda owner: ForegroundConflict(owner),
        )

    @property
    def path(self) -> Path:
        """返回底层数据库文件路径。"""

        return self._path

    def open(self) -> None:
        """打开连接，并在需要时初始化 schema。"""

        self._conn = self._create_connection()
        self._bootstrap()

    def close(self) -> None:
        """关闭连接；重复调用不产生影响。"""

        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _create_connection(self) -> sqlite3.Connection:
        """创建可跨 FastAPI worker 线程使用的 WAL connection。

        ``check_same_thread=False`` 允许 TestClient 的 anyio portal 线程访问；
        结构化命令的原子性由 ``_tx`` 显式 ``BEGIN IMMEDIATE`` 保证。
        ``isolation_level="IMMEDIATE"`` 仍覆盖直接使用 connection context 的
        bootstrap/兼容路径，lease CAS 的最终仲裁由 partial unique index 完成。
        """

        conn = sqlite3.connect(str(self._path), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.isolation_level = "IMMEDIATE"
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _bootstrap(self) -> None:
        """创建缺失的表和索引，并写入 schema 版本。

        DDL 通过带 ``IF NOT EXISTS`` 的 ``executescript`` 幂等执行；版本用参数化
        ``execute`` 单独写入。旧库随后由 ``_migrate_schema`` 显式添加列或重建索引。
        """

        assert self._conn is not None
        # ``executescript`` 自行管理事务，因此 bootstrap 直接使用连接上下文，不能与
        # ``_tx`` 发出的显式 ``BEGIN`` 叠加。
        with self._conn:
            self._conn.executescript(_SCHEMA_SQL)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES (?, ?)",
                ("schema_version", str(_SCHEMA_VERSION)),
            )
            # foreground_claim 固定为单行；预置 ``id=1`` 和空 ``task_id``，使基于
            # UPDATE 的 CAS 可用，并保证重启后始终有且仅有一行可读。
            self._conn.execute(
                "INSERT OR IGNORE INTO foreground_claim (id, task_id) VALUES (1, NULL)"
            )
            self._migrate_schema()

    def _migrate_schema(self) -> None:
        """按 ``meta.schema_version`` 对旧库执行前向迁移。

        新库已由 ``executescript`` 写入当前版本，此处不操作；旧库保留较小版本号，
        需要在这里显式执行 ``ALTER`` 或重建索引。
        """

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        current = int(row["value"]) if row is not None else _SCHEMA_VERSION
        if current < 2:
            # v1 → v2：旧库补充 ``leases.fencing_token``，并把
            # ``idx_leases_idem`` 改为资源域唯一。SQLite 不能直接替换索引，只能删除重建。
            cols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(leases)").fetchall()
            ]
            if "fencing_token" not in cols:
                self._conn.execute(
                    "ALTER TABLE leases ADD COLUMN fencing_token INTEGER NOT NULL DEFAULT 0"
                )
            self._conn.execute("DROP INDEX IF EXISTS idx_leases_idem")
            self._conn.execute(
                "CREATE UNIQUE INDEX idx_leases_idem "
                "ON leases(resource_type, resource_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL"
            )
            # 版本必须在迁移分支内落盘；若先把 ``current`` 改为 2 再和当前版本比较，
            # 写入会被跳过，导致每次重开都重复迁移。
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                ("2",),
            )
            current = 2
        if current < 3:
            # v2 → v3：在事件行持久化 fencing lease 三元组。旧库逐列补齐；非 fencing
            # 事件允许三列均为空。
            ev_cols = [
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(events)").fetchall()
            ]
            for col in ("lease_id", "owner", "fencing_token"):
                if col not in ev_cols:
                    self._conn.execute(
                        f"ALTER TABLE events ADD COLUMN {col} "
                        f"{'INTEGER' if col == 'fencing_token' else 'TEXT'}"
                    )
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                ("3",),
            )
            current = 3
        if current < 4:
            # v3 → v4：``idx_leases_idem`` 只约束未释放 lease，使旧 lease 的幂等键不
            # 阻塞新授权。接管会保留并释放旧行，再插入新行保存授权历史。
            self._conn.execute("DROP INDEX IF EXISTS idx_leases_idem")
            self._conn.execute(
                "CREATE UNIQUE INDEX idx_leases_idem "
                "ON leases(resource_type, resource_id, idempotency_key) "
                "WHERE idempotency_key IS NOT NULL AND released_at IS NULL"
            )
            self._conn.execute(
                "UPDATE meta SET value=? WHERE key='schema_version'",
                ("4",),
            )

    def _schema_version(self) -> int:
        """返回 ``meta`` 中记录的 schema 版本。"""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row["value"]) if row is not None else _SCHEMA_VERSION

    def create_work_item(
        self,
        *,
        kind: WorkItemKind,
        owner_ref: str,
        task_id: str | None,
        session_purpose: SessionPurpose,
        memory_eligibility: MemoryEligibility,
    ) -> WorkItem:
        """创建合法的非 Task WorkItem，并把事实写入 journal。

        WorkItem 从 PENDING 开始，由结构化命令推进生命周期。本入口拒绝 ``TASK``；
        Task 与主 WorkItem 必须由 ``create_task_from_user_request`` 原子创建并保持一对一，
        不能让调用方为同一 Task 创建多个主 WorkItem 或绑定不存在的 Task。
        """

        if kind == WorkItemKind.TASK:
            raise TaskCommandError(
                "TASK WorkItems must be created via "
                "create_task_from_user_request (slice-086: Task↔primary "
                "WorkItem is 1:1)"
            )
        _validate_work_item(kind, task_id)
        work_item_id = uuid4().hex
        created_at = _now_iso()
        work_item = WorkItem(
            work_item_id=work_item_id,
            kind=kind,
            owner_ref=owner_ref,
            task_id=task_id,
            status=WorkItemStatus.PENDING,
            session_purpose=session_purpose,
            memory_eligibility=memory_eligibility,
            created_at=created_at,
        )
        event = EventEnvelope(
            event_id=f"wi.create.{work_item_id}",
            kind=EventKind.WORK_ITEM_CREATED,
            occurred_at=created_at,
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={
                "work_item_id": work_item_id,
                "kind": kind.value,
                "owner_ref": owner_ref,
                "task_id": task_id,
                "status": WorkItemStatus.PENDING.value,
                "session_purpose": session_purpose.value,
                "memory_eligibility": memory_eligibility.value,
            },
            work_item_id=work_item_id,
            task_id=task_id,
        )
        self.append_event(event)
        return work_item

    def acquire_lease(
        self,
        *,
        resource_type: str,
        resource_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None = None,
    ) -> Lease:
        """以 CAS 原子取得携带 fencing token 的 lease。

        其他活跃 lease 已占用资源时抛出 ``LeaseConflict``，过期 lease 则原子接管。
        ``idempotency_key`` 以 ``(resource_type, resource_id)`` 为作用域：同 owner
        返回原 lease，不同 owner 冲突，其他资源可独立复用该键。每次授权从
        ``lease_fence_counters`` 取得严格递增的 token，使接管前的持有者无法继续写入。
        """

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()

        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type=? AND resource_id=? "
                "AND idempotency_key=? AND released_at IS NULL",
                (resource_type, resource_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict(resource_type, resource_id)
                # 幂等重试不得返回已过期 lease；TTL 后到达视为冲突，调用方必须重新取得，
                # 否则第一次受 fencing 保护的写入必然失败。
                if existing["expires_at"] <= now_str:
                    raise LeaseConflict(resource_type, resource_id)
                return _lease_from_row(existing)

        try:
            with self._tx():
                token = self._next_fence_token_in_tx(resource_type, resource_id)
                lease_id = uuid4().hex
                self._conn.execute(
                    "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                    "acquired_at, expires_at, idempotency_key, released_at, "
                    "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        lease_id,
                        resource_type,
                        resource_id,
                        owner,
                        now_str,
                        expires_str,
                        idempotency_key,
                        token,
                    ),
                )
                return Lease(
                    lease_id=lease_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    owner=owner,
                    acquired_at=now_str,
                    expires_at=expires_str,
                    idempotency_key=idempotency_key,
                    fencing_token=token,
                )
        except sqlite3.IntegrityError:
            return self._takeover_or_conflict(
                resource_type,
                resource_id,
                owner,
                now_str,
                expires_str,
                idempotency_key,
            )

    def _next_fence_token_in_tx(self, resource_type: str, resource_id: str) -> int:
        """在调用方事务内递增并返回资源的下一个 fencing token。

        计数器独立于 ``leases`` 行，释放或清理旧 lease 不会令 token 回退。计数器
        UPSERT 与 lease INSERT 共享一个 IMMEDIATE 事务，并发授权因而严格串行；
        授权失败会连同递增一起回滚，不消耗 token。
        """

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT last_token FROM lease_fence_counters "
            "WHERE resource_type=? AND resource_id=?",
            (resource_type, resource_id),
        ).fetchone()
        new_token = (int(row["last_token"]) + 1) if row is not None else 1
        self._conn.execute(
            "INSERT INTO lease_fence_counters (resource_type, resource_id, last_token) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(resource_type, resource_id) "
            "DO UPDATE SET last_token=excluded.last_token",
            (resource_type, resource_id, new_token),
        )
        return new_token

    def _takeover_or_conflict(
        self,
        resource_type: str,
        resource_id: str,
        owner: str,
        now_str: str,
        expires_str: str,
        idempotency_key: str | None,
    ) -> Lease:
        """处理 INSERT 冲突：幂等取回、接管过期 lease 或抛出 ``LeaseConflict``。

        接管不会原地更新旧行，而是先标记释放，再以更高 fencing token 插入新行，
        从而保留旧授权及其幂等键的审计历史。
        """

        assert self._conn is not None
        if idempotency_key is not None:
            row = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type=? AND resource_id=? "
                "AND idempotency_key=? AND released_at IS NULL",
                (resource_type, resource_id, idempotency_key),
            ).fetchone()
            if row is not None:
                if row["owner"] != owner:
                    raise LeaseConflict(resource_type, resource_id)
                return _lease_from_row(row)
        existing = self._conn.execute(
            "SELECT * FROM leases WHERE resource_type=? AND resource_id=? "
            "AND released_at IS NULL",
            (resource_type, resource_id),
        ).fetchone()
        if existing is not None and existing["expires_at"] <= now_str:
            with self._tx():
                # 先释放过期授权，再插入新行。部分唯一索引 ``idx_leases_active`` 在
                # UPDATE 生效时释放占位，新行不会与旧行冲突。到期边界使用 ``<=``，
                # 与 fencing 校验一致：旧 owner 在精确到期时刻失权，新 owner 可立即接管。
                cur = self._conn.execute(
                    "UPDATE leases SET released_at=? "
                    "WHERE resource_type=? AND resource_id=? AND released_at IS NULL "
                    "AND expires_at <= ?",
                    (now_str, resource_type, resource_id, now_str),
                )
                if cur.rowcount == 1:
                    token = self._next_fence_token_in_tx(resource_type, resource_id)
                    new_lease_id = uuid4().hex
                    try:
                        self._conn.execute(
                            "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                            "acquired_at, expires_at, idempotency_key, released_at, "
                            "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                            (
                                new_lease_id,
                                resource_type,
                                resource_id,
                                owner,
                                now_str,
                                expires_str,
                                idempotency_key,
                                token,
                            ),
                        )
                    except sqlite3.IntegrityError as exc:
                        # 幂等键仍被占用或其他写入者赢得竞争时，统一暴露
                        # ``LeaseConflict``，不泄漏底层 sqlite3 异常。
                        raise LeaseConflict(resource_type, resource_id) from exc
                    return Lease(
                        lease_id=new_lease_id,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        owner=owner,
                        acquired_at=now_str,
                        expires_at=expires_str,
                        idempotency_key=idempotency_key,
                        fencing_token=token,
                    )
        raise LeaseConflict(resource_type, resource_id)

    def release_lease(self, lease_id: str) -> bool:
        """按 ID 释放 lease；仅实际释放活跃 lease 时返回 ``True``。"""

        assert self._conn is not None
        with self._tx():
            cur = self._conn.execute(
                "UPDATE leases SET released_at=? WHERE lease_id=? AND released_at IS NULL",
                (_now_iso(), lease_id),
            )
            return cur.rowcount == 1

    def _read_active_leases(self) -> tuple[Lease, ...]:
        assert self._conn is not None
        now_str = _now_iso()
        rows = self._conn.execute(
            "SELECT * FROM leases WHERE released_at IS NULL AND expires_at > ? "
            "ORDER BY acquired_at",
            (now_str,),
        ).fetchall()
        return tuple(_lease_from_row(r) for r in rows)

    def append_event(self, event: EventEnvelope) -> int:
        """按 ``event_id`` 幂等追加事件并返回 seq。

        payload 在接触 SQLite 前先脱敏。同 ID 且完整身份一致时返回原 seq；同 ID
        但内容不同则抛出 ``ValueError``。Task 与 Episode 生命周期事件必须经过
        结构化命令，持有 Store 的调用方不能绕过状态和 fencing 门禁。
        """

        assert self._conn is not None
        if event.kind in _TASK_LIFECYCLE_KINDS:
            raise TaskCommandError(
                f"event kind {event.kind!r} is a Task lifecycle event; use "
                f"the corresponding structured command (provenance is not an "
                f"authorisation mechanism)"
            )
        if event.kind in _EPISODE_LIFECYCLE_KINDS:
            raise EpisodeCommandError(
                f"event kind {event.kind!r} is an Episode lifecycle event; "
                f"use the corresponding structured command (slice-087)"
            )
        payload_text, payload_hash = _payload_json(event.payload)
        try:
            with self._tx():
                self._conn.execute(
                    _EVENT_INSERT_SQL,
                    _event_params(event, payload_text, payload_hash),
                )
        except sqlite3.IntegrityError:
            # 重复 event_id 只有完整身份一致时才幂等；若只比较 ``payload_hash``，
            # 不同 kind、实体或 lease 三元组会错误指向首条事件。
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
            if existing is None or _event_row_identity(
                existing, payload_hash
            ) != _event_identity(event, payload_hash):
                raise ValueError(
                    f"event_id {event.event_id!r} already exists with "
                    f"different content (identity mismatch)"
                )
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    # Context 样本是被动观测而非生命周期变更，因此通过 ``append_event`` 写入，并
    # 依赖 ``event_id`` 幂等，无需结构化命令门禁。
    def record_context_sample(
        self,
        sample: ContextSample,
        *,
        episode_id: str | None,
        occurred_at: str,
        task_id: str | None = None,
        source: str = "observer",
    ) -> int:
        """按观测身份幂等追加 ``CONTEXT_SAMPLE_OBSERVED`` 事件。

        ``event_id`` 由 Episode、原生 session、请求身份和 generation 构成；
        ``occurred_at`` 仅持久化，不参与身份。Episode 归属只信任 envelope。
        """

        # ``occurred_at`` 不得进入 event_id。同一消息的流式 usage 由批量计算器保留
        # 最后一条，使一次请求对应一个样本和稳定 ID；幂等重试必须保持完整 envelope 一致。
        event_id = (
            f"ctx.sample.{episode_id or 'no-ep'}.{sample.native_session_id}."
            f"{sample.request_identity}.{sample.generation}"
        )
        event = EventEnvelope(
            event_id=event_id,
            kind=EventKind.CONTEXT_SAMPLE_OBSERVED,
            occurred_at=occurred_at,
            source=source,
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload=context_sample_to_dict(sample),
            task_id=task_id,
            episode_id=episode_id,
            native_session_id=sample.native_session_id,
        )
        return self.append_event(event)

    def record_context_boundary(
        self,
        native_session_id: str,
        *,
        episode_id: str | None,
        generation: int,
        occurred_at: str,
        trigger: str | None = None,
        task_id: str | None = None,
        source: str = "observer",
    ) -> int:
        """追加只供 reducer 审计的 ``CONTEXT_GENERATION_BOUNDARY`` 事件。

        边界事件必须先于边界后的样本落盘，确保回放重建相同 generation 顺序。
        ``generation`` 在此只供审计，ContextSample 的派生值来自计算器。
        """

        event_id = (
            f"ctx.boundary.{episode_id or 'no-ep'}.{native_session_id}.{generation}"
        )
        event = EventEnvelope(
            event_id=event_id,
            kind=EventKind.CONTEXT_GENERATION_BOUNDARY,
            occurred_at=occurred_at,
            source=source,
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={"generation": generation, "trigger": trigger},
            task_id=task_id,
            episode_id=episode_id,
            native_session_id=native_session_id,
        )
        return self.append_event(event)

    def append_decision(self, decision: DecisionRecord) -> int:
        """按 ``decision_id`` 幂等追加决策并返回 seq。

        ``signals``、``candidates``、``reason`` 和 ``budget_*`` 在持久化前脱敏，
        决策不能绕过事件日志的隐私边界。
        """

        assert self._conn is not None
        try:
            with self._tx():
                self._conn.execute(_DECISION_INSERT_SQL, _decision_params(decision))
        except sqlite3.IntegrityError:
            pass
        row = self._conn.execute(
            "SELECT seq FROM decisions WHERE decision_id=?",
            (decision.decision_id,),
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    def append_decision_with_intent(
        self, decision: DecisionRecord, intent_event: EventEnvelope
    ) -> tuple[int, int]:
        """原子追加决策及其触发的 intent 事件。

        二者在同一事务内按“决策先于命令”顺序落盘，崩溃不会留下单边记录。两个 ID
        都存在时返回原 seq；只存在一侧说明原子对已分裂，必须显式报错。
        """

        assert self._conn is not None
        # 此公开入口必须复用 ``append_event`` 的生命周期门禁，否则调用方可伪造
        # 生命周期 intent，绕过结构化命令和 fencing，并在回放时污染权威状态。
        if intent_event.kind in _TASK_LIFECYCLE_KINDS:
            raise TaskCommandError(
                f"intent event kind {intent_event.kind!r} is a Task lifecycle "
                f"event; use the corresponding structured command"
            )
        if intent_event.kind in _EPISODE_LIFECYCLE_KINDS:
            raise EpisodeCommandError(
                f"intent event kind {intent_event.kind!r} is an Episode "
                f"lifecycle event; use the corresponding structured command "
                f"(slice-087)"
            )
        payload_text, payload_hash = _payload_json(intent_event.payload)
        try:
            with self._tx():
                self._conn.execute(_DECISION_INSERT_SQL, _decision_params(decision))
                self._conn.execute(
                    _EVENT_INSERT_SQL,
                    _event_params(intent_event, payload_text, payload_hash),
                )
        except sqlite3.IntegrityError:
            if not self._pair_already_present(
                decision.decision_id, intent_event.event_id
            ):
                raise
        d_row = self._conn.execute(
            "SELECT seq FROM decisions WHERE decision_id=?",
            (decision.decision_id,),
        ).fetchone()
        e_row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (intent_event.event_id,)
        ).fetchone()
        assert d_row is not None and e_row is not None
        return int(d_row["seq"]), int(e_row["seq"])

    def _pair_already_present(self, decision_id: str, event_id: str) -> bool:
        assert self._conn is not None
        d = self._conn.execute(
            "SELECT 1 FROM decisions WHERE decision_id=?", (decision_id,)
        ).fetchone()
        e = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return d is not None and e is not None

    def list_events(self, from_seq: int = 0) -> list[tuple[int, EventEnvelope]]:
        """按序返回满足 ``seq > from_seq`` 的 ``(seq, event)``。"""

        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM events WHERE seq > ? ORDER BY seq",
            (from_seq,),
        ).fetchall()
        return [(int(row["seq"]), _event_from_row(row)) for row in rows]

    def list_decisions(self, from_seq: int = 0) -> list[tuple[int, DecisionRecord]]:
        """按序返回满足 ``seq > from_seq`` 的 ``(seq, decision)``。"""

        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE seq > ? ORDER BY seq",
            (from_seq,),
        ).fetchall()
        return [(int(row["seq"]), _decision_from_row(row)) for row in rows]

    def replay(self, from_seq: int = 0) -> Snapshot:
        """归约 ``from_seq`` 之后的事件并返回派生快照。

        ``from_seq`` 表示已经归约的最后一个 seq，默认为 0，即完整回放。实时表中的
        ``active_leases`` 不在此装载，由 ``read_snapshot`` 合并。
        """

        snap = initial_snapshot(schema_version=self._schema_version())
        for seq, event in self.list_events(from_seq=from_seq):
            snap = reduce_event(snap, event)
            snap = replace(snap, last_seq=seq)
        return snap

    # Task 结构化命令在一个 IMMEDIATE 事务内完成回放、校验、journal 追加以及必要的
    # foreground_claim 更新。门禁依据命令身份而非 provenance。随机 event_id 使崩溃
    # 重试可能留下重复状态审计事件，但 reducer 对状态赋值幂等；Task 创建另由
    # ``task_create_keys`` 保证完全幂等。

    @contextmanager
    def _tx(self):
        """显式开启 IMMEDIATE 事务，使回放读取与后续写入共享同一快照。

        仅设置 ``isolation_level`` 会到首次 DML 才自动开始事务，命令开头的回放可能
        读到随后失效的计数。显式 ``BEGIN IMMEDIATE`` 同时串行化写入者。``RLock``
        防止共享连接的请求跨线程交错，并允许同线程 helper 重入已有事务。
        """

        assert self._conn is not None
        with self._lock:
            if self._conn.in_transaction:
                yield
                return
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.execute("COMMIT")
            except BaseException as exc:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                # 陈旧写入事务回滚后，仍须在独立事务中持久化
                # ``late_write_rejected``；审计失败不能掩盖原始拒绝异常。
                if isinstance(exc, StaleWriterRejected):
                    attempted = getattr(exc, "attempted_event", None)
                    if attempted is not None:
                        try:
                            self._conn.execute("BEGIN IMMEDIATE")
                            self._reject_stale_write_in_tx(attempted, exc)
                            self._conn.execute("COMMIT")
                        except BaseException:
                            # 审计存储失败会写日志以保持可观测性，最终仍抛出原始 ``StaleWriterRejected``。
                            _LOGGER.warning(
                                "late_write_rejected audit failed; the "
                                "original StaleWriterRejected is preserved",
                                exc_info=True,
                            )
                            if self._conn.in_transaction:
                                self._conn.execute("ROLLBACK")
                raise

    @contextmanager
    def _read_tx(self):
        """用 DEFERRED 事务让 replay、lease 和 foreground 读取共享同一快照。

        若三次 SELECT 分别自动提交，并发 claim 或 release 可返回割裂状态。DEFERRED
        只取得共享锁，不阻塞 WAL 写入者，但并发提交不能再切开这组读取。
        """

        assert self._conn is not None
        with self._lock:
            if self._conn.in_transaction:
                yield
                return
            self._conn.execute("BEGIN")
            try:
                yield
                self._conn.execute("COMMIT")
            except BaseException:
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def _read_foreground_task_id(self) -> str | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT task_id FROM foreground_claim WHERE id=1"
        ).fetchone()
        return None if row is None else row["task_id"]

    def read_foreground_task_id(self) -> str | None:
        """读取当前 foreground ``task_id``；未占用时返回 ``None``。"""

        return self._read_foreground_task_id()

    def _insert_event_in_tx(self, event: EventEnvelope) -> int | None:
        """在调用方事务中追加事件。

        返回新 seq；重复 event_id 的幂等跳过返回 ``None``。此方法不能另开连接事务，
        否则嵌套连接上下文会提前提交外层工作。
        """

        assert self._conn is not None
        payload_text, payload_hash = _payload_json(event.payload)
        try:
            self._conn.execute(
                _EVENT_INSERT_SQL,
                _event_params(event, payload_text, payload_hash),
            )
        except sqlite3.IntegrityError:
            # 重复 event_id 只有完整身份一致时才幂等；不同事件复用 ID 必须显式报错。
            existing = self._conn.execute(
                "SELECT * FROM events WHERE event_id=?", (event.event_id,)
            ).fetchone()
            if existing is None or _event_row_identity(
                existing, payload_hash
            ) != _event_identity(event, payload_hash):
                raise ValueError(
                    f"event_id {event.event_id!r} already exists with "
                    f"different content (identity mismatch)"
                )
            return None
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    def _require_task(self, snap: Snapshot, task_id: str) -> TaskState:
        task = next((t for t in snap.tasks if t.task_id == task_id), None)
        if task is None:
            raise TaskCommandError(f"unknown task_id={task_id!r}")
        return task

    def _require_non_terminal(self, task: TaskState) -> None:
        if task.status.is_terminal:
            raise TaskCommandError(
                f"task {task.task_id!r} is terminal ({task.status.value})"
            )

    def _require_status(self, task: TaskState, allowed: set[TaskStatus]) -> None:
        if task.status not in allowed:
            allowed_str = sorted(s.value for s in allowed)
            raise TaskCommandError(
                f"task {task.task_id!r} status {task.status.value} not in "
                f"allowed source states {allowed_str}"
            )

    @staticmethod
    def _task_state_to_task(state: TaskState) -> Task:
        """把 reducer 的 ``TaskState`` 投影为公开 ``Task``，忽略审计来源。"""

        task_type = Task
        return _run_project_task_state(state, task_type=task_type)

    def _make_task_event(
        self,
        kind: str,
        task_id: str,
        payload: dict[str, Any],
        provenance: Provenance = Provenance.MACHINE_OBSERVATION,
        work_item_id: str | None = None,
    ) -> EventEnvelope:
        event_type = EventEnvelope
        event_id = f"{kind}.{uuid4().hex}"
        occurred_at = _now_iso()
        return _run_make_task_event(
            kind,
            task_id,
            payload,
            event_id=event_id,
            occurred_at=occurred_at,
            provenance=provenance,
            work_item_id=work_item_id,
            policy_version=self._policy_version,
            event_type=event_type,
        )

    def _work_item_status_event(
        self,
        work_item_id: str,
        new_status: WorkItemStatus,
        task_id: str | None,
        now: str,
    ) -> EventEnvelope:
        """构造同步主 WorkItem 与 Task 状态的变更事件。"""

        event_type = EventEnvelope
        event_id = f"wi.status.{work_item_id}.{uuid4().hex}"
        event_kind = EventKind.WORK_ITEM_STATUS_CHANGED
        provenance = Provenance.MACHINE_OBSERVATION
        return _run_make_work_item_event(
            work_item_id,
            new_status,
            task_id,
            now,
            event_id=event_id,
            event_kind=event_kind,
            provenance=provenance,
            policy_version=self._policy_version,
            event_type=event_type,
        )

    def _release_foreground_in_tx(self, task_id: str) -> None:
        """以 CAS 清除 ``task_id`` 持有的 foreground，并写入释放审计事件。

        调用方持有事务。只有当前 owner 匹配时才更新并发出事件，避免复合 Episode
        命令在未预检时错误清除其他 Task 的 foreground。
        """

        assert self._conn is not None
        cur = self._conn.execute(
            "UPDATE foreground_claim SET task_id=NULL WHERE id=1 AND task_id=?",
            (task_id,),
        )
        if cur.rowcount == 1:
            self._insert_event_in_tx(
                self._make_task_event(EventKind.FOREGROUND_RELEASED, task_id, {})
            )

    def create_task_from_user_request(
        self,
        *,
        original_goal: str,
        idempotency_key: str,
        authorization_scope: str = "",
        priority: int = 0,
    ) -> Task:
        """原子且幂等地创建 Task 及其主 WorkItem。

        此可信边界固定写入 ``USER_DECISION``，调用方不能伪造。相同幂等键重试返回
        首次创建的 Task，不新增主 WorkItem，也不覆盖首次参数；修改已有 Task 应调用
        ``append_constraint`` 或 ``change_authorization``。
        """

        return self._task_commands.create_task_from_user_request(
            original_goal=original_goal,
            idempotency_key=idempotency_key,
            authorization_scope=authorization_scope,
            priority=priority,
        )

    def promote_to_warm(self, task_id: str) -> None:
        """把 BACKLOG Task 提升为 warm READY。

        warm 池满时抛出 ``WarmFull``，不会自动替换已有 Task；已是 warm 时幂等返回。
        """

        self._task_commands.promote_to_warm(task_id)

    def demote_to_backlog(self, task_id: str) -> None:
        """把 warm Task 降为 BACKLOG；持有 foreground 时必须先释放。"""

        self._task_commands.demote_to_backlog(task_id)

    def claim_foreground(self, task_id: str) -> None:
        """原子占用 foreground，并将 Task 与主 WorkItem 置为运行态。

        Task 必须已 warm；其他 Task 持有时抛出 ``ForegroundConflict``，同一 Task
        重复占用时幂等返回。
        """

        self._task_commands.claim_foreground(task_id)

    def release_foreground(self) -> None:
        """原子释放 foreground，并把非终态 Task 与主 WorkItem 恢复为 READY。

        未占用 foreground 时幂等返回。
        """

        self._task_commands.release_foreground()

    def _set_waiting(self, task_id: str, waiting: WaitingCondition) -> None:
        with self._tx():
            self._set_waiting_in_tx(task_id, waiting)

    def _set_waiting_in_tx(
        self,
        task_id: str,
        waiting: WaitingCondition,
        snap: Snapshot | None = None,
    ) -> None:
        """在调用方事务内把 Task 置为等待态。

        ``suspend_episode`` 借此原子提交 Episode 暂停、Task 等待、foreground CAS
        与 WorkItem SUSPENDED。事件中的 ``subtype``、``episode_id`` 必须与所属
        Episode 的 pending 一致，其他入口可传 ``None``。传入已回放的 ``snap`` 可
        避免在 IMMEDIATE 锁内重复归约长 journal。
        """

        assert self._conn is not None
        if snap is None:
            snap = self.replay()
        task = self._require_task(snap, task_id)
        self._require_non_terminal(task)
        self._require_status(task, {TaskStatus.RUNNING})
        now = _now_iso()
        if self._read_foreground_task_id() == task_id:
            self._release_foreground_in_tx(task_id)
        if task.primary_work_item_id:
            self._insert_event_in_tx(
                self._work_item_status_event(
                    task.primary_work_item_id,
                    WorkItemStatus.SUSPENDED,
                    task_id,
                    now,
                )
            )
        self._insert_event_in_tx(
            self._make_task_event(
                EventKind.TASK_WAITING_SET,
                task_id,
                {
                    "kind": waiting.kind,
                    "cause": waiting.cause,
                    "subtype": waiting.subtype.value if waiting.subtype else None,
                    "episode_id": waiting.episode_id,
                    "correlation_id": waiting.correlation_id,
                    "deadline": waiting.deadline,
                    "condition_kind": waiting.condition_kind,
                    "target_ref": waiting.target_ref,
                    "match_params": waiting.match_params,
                    "open_question": waiting.open_question,
                    "preparation_snapshot_ref": waiting.preparation_snapshot_ref,
                    "earliest_review_at": waiting.earliest_review_at,
                },
            )
        )

    def set_waiting_user(
        self,
        task_id: str,
        *,
        cause: str,
        correlation_id: str,
        deadline: str | None = None,
    ) -> None:
        """把运行中 Task 置为 WAITING_USER 并释放 foreground。

        ``correlation_id`` 必填，用于关联唤醒该 Task 的用户回复。
        """

        self._task_commands.set_waiting_user(
            task_id,
            cause=cause,
            correlation_id=correlation_id,
            deadline=deadline,
        )

    def set_waiting_event(
        self,
        task_id: str,
        *,
        cause: str,
        condition_kind: str,
        target_ref: str,
        match_params: dict[str, Any] | None = None,
        deadline: str | None = None,
    ) -> None:
        """把运行中 Task 置为 WAITING_EVENT；外部条件的种类和目标均必填。"""

        self._task_commands.set_waiting_event(
            task_id,
            cause=cause,
            condition_kind=condition_kind,
            target_ref=target_ref,
            match_params=match_params,
            deadline=deadline,
        )

    def set_incubating(
        self,
        task_id: str,
        *,
        open_question: str,
        preparation_snapshot_ref: str,
        earliest_review_at: str | None = None,
    ) -> None:
        """把运行中 Task 置为 INCUBATING；必须提供未解问题和准备快照引用。"""

        self._task_commands.set_incubating(
            task_id,
            open_question=open_question,
            preparation_snapshot_ref=preparation_snapshot_ref,
            earliest_review_at=earliest_review_at,
        )

    def clear_waiting(self, task_id: str) -> None:
        """清除等待条件，并把 WAITING_* Task 恢复为 READY。"""

        self._task_commands.clear_waiting(task_id)

    def complete_task(
        self,
        task_id: str,
        *,
        confirmed_by: str,
        evidence_refs: tuple[str, ...] = (),
        confirmation_provenance: Provenance = Provenance.USER_DECISION,
    ) -> None:
        """把运行中 Task 标记为 DONE，并记录确认者、证据和来源。

        ``USER_REQUEST`` 只能由 ``USER_DECISION`` 确认；模型自报不能关闭用户 Task。
        foreground 在同一事务内释放。
        """

        self._task_commands.complete_task(
            task_id,
            confirmed_by=confirmed_by,
            evidence_refs=evidence_refs,
            confirmation_provenance=confirmation_provenance,
        )

    def cancel_task(self, task_id: str, *, reason: str) -> None:
        """在同一事务内取消 Task、更新主 WorkItem 并释放 foreground。"""

        self._task_commands.cancel_task(task_id, reason=reason)

    def record_task_error(
        self,
        task_id: str,
        *,
        reason: str,
        last_snapshot_ref: str | None = None,
        last_episode_ref: str | None = None,
        recovery_hint: str | None = None,
    ) -> None:
        """记录 Task 级终态失败，并把主 WorkItem 置为 FAILED。

        保留 ``last_snapshot_ref`` 供后续重新打开，并在同一事务释放 foreground。
        Episode 或工具的瞬时失败不走此入口，而应把 Task 恢复为 READY 后重试。
        """

        self._task_commands.record_task_error(
            task_id,
            reason=reason,
            last_snapshot_ref=last_snapshot_ref,
            last_episode_ref=last_episode_ref,
            recovery_hint=recovery_hint,
        )

    def append_constraint(self, task_id: str, constraint: str) -> None:
        """追加用户澄清的约束，不修改 ``original_goal``。"""

        self._task_commands.append_constraint(task_id, constraint)

    def set_warm_rank(self, task_id: str, warm_rank: int | None) -> None:
        self._task_commands.set_warm_rank(task_id, warm_rank)

    def change_authorization(
        self,
        task_id: str,
        *,
        authorization_scope: str,
        confirmed_by: str,
    ) -> None:
        """修改 Task 的授权范围，并把 ``confirmed_by`` 写入审计事件。

        仅可信内核可在用户决策后调用；权限来自命令边界，不依赖调用方可自报的
        provenance。终态 Task 拒绝修改。
        """

        self._task_commands.change_authorization(
            task_id,
            authorization_scope=authorization_scope,
            confirmed_by=confirmed_by,
        )

   # Episode 的受 fencing 保护命令在同一 IMMEDIATE 事务内完成状态回放、ownership
    # 三元组校验、journal 追加以及快照行或 lease 更新。保护范围由事件 kind 强制，
    # 陈旧写入者不能通过省略 token 绕过。

    _EPISODE_OWNERSHIP_RESOURCE_TYPE = "episode_ownership"

    def _read_episode_lease_row(self, episode_id: str) -> sqlite3.Row | None:
        assert self._conn is not None
        return self._conn.execute(
            "SELECT * FROM leases WHERE resource_type='episode_ownership' "
            "AND resource_id=? AND released_at IS NULL",
            (episode_id,),
        ).fetchone()

    def _check_ownership_in_tx(
        self,
        episode_id: str,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """校验调用方确实持有 Episode 的实时 ownership lease。

        ``lease_id``、``owner``、``fencing_token`` 必须全部匹配，且 lease 未释放、
        未过期，否则抛出 ``StaleWriterRejected``。权限在到期瞬间终止，无需等待接管。
        """

        assert self._conn is not None
        row = self._read_episode_lease_row(episode_id)
        now_str = _now_iso()
        if row is None:
            raise StaleWriterRejected(
                episode_id, "no active ownership lease", expected_token, None
            )
        current_token = int(row["fencing_token"])
        if row["lease_id"] != expected_lease_id:
            raise StaleWriterRejected(
                episode_id, "lease_id mismatch", expected_token, current_token
            )
        if row["owner"] != expected_owner:
            raise StaleWriterRejected(
                episode_id, "owner mismatch", expected_token, current_token
            )
        if current_token != expected_token:
            raise StaleWriterRejected(
                episode_id,
                "fencing_token mismatch (stale writer)",
                expected_token,
                current_token,
            )
        if row["expires_at"] <= now_str:
            raise StaleWriterRejected(
                episode_id, "lease expired", expected_token, current_token
            )

    def _reject_stale_write_in_tx(
        self, event: EventEnvelope, exc: StaleWriterRejected
    ) -> None:
        """在调用方事务内记录陈旧写入拒绝事实，不改变权威状态。"""

        assert self._conn is not None
        audit = EventEnvelope(
            event_id=f"late_write.{uuid4().hex}",
            kind=EventKind.LATE_WRITE_REJECTED,
            occurred_at=_now_iso(),
            source="kernel",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version=self._policy_version,
            payload={
                "episode_id": exc.episode_id,
                "attempted_kind": event.kind,
                "attempted_event_id": event.event_id,
                "attempted_token": exc.attempted_token,
                "current_token": exc.current_token,
                "reason": exc.reason,
            },
            episode_id=exc.episode_id,
        )
        payload_text, payload_hash = _payload_json(audit.payload)
        self._conn.execute(
            _EVENT_INSERT_SQL, _event_params(audit, payload_text, payload_hash)
        )

    def _append_fenced_event_in_tx(self, event: EventEnvelope) -> int:
        """追加受 fencing 保护的 Episode 事件。

        若 ``event_id`` 对应的完整事件身份已存在，直接返回原 seq，不再校验实时
        lease，使过期调用方也能只读重试；同 ID 不同内容抛出 ``ValueError``。新事件
        必须先通过 ownership 校验，陈旧写入由外层事务回滚后另记拒绝审计。
        """

        assert self._conn is not None
        if event.kind not in _EPISODE_FENCED_KINDS:
            raise EpisodeCommandError(
                f"_append_fenced_event_in_tx called with non-fenced kind {event.kind!r}"
            )
        if (
            event.episode_id is None
            or event.lease_id is None
            or event.owner is None
            or event.fencing_token is None
        ):
            raise EpisodeCommandError(
                f"fenced event {event.kind!r} must carry episode_id, lease_id, "
                f"owner, fencing_token"
            )
        payload_text, payload_hash = _payload_json(event.payload)
        existing = self._conn.execute(
            "SELECT * FROM events WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
            # 只有 kind、实体关联、lease 三元组和 payload_hash 全部一致才是幂等重试。
            if _event_row_identity(existing, payload_hash) != _event_identity(
                event, payload_hash
            ):
                raise ValueError(
                    f"event_id {event.event_id!r} already exists with different "
                    f"content (identity mismatch)"
                )
            return int(existing["seq"])
        try:
            self._check_ownership_in_tx(
                event.episode_id, event.lease_id, event.owner, event.fencing_token
            )
        except StaleWriterRejected as exc:
            # 当前事务即将回滚，不能在这里写审计；把尝试事件附到异常，由最外层
            # ``_tx`` 回滚后在新事务中记录。
            exc.attempted_event = event  # type: ignore[attr-defined]
            raise
        self._conn.execute(
            _EVENT_INSERT_SQL, _event_params(event, payload_text, payload_hash)
        )
        row = self._conn.execute(
            "SELECT seq FROM events WHERE event_id=?", (event.event_id,)
        ).fetchone()
        assert row is not None
        return int(row["seq"])

    def _make_episode_event(
        self,
        kind: str,
        episode_id: str,
        payload: dict[str, Any],
        *,
        work_item_id: str | None = None,
        task_id: str | None = None,
        provenance: Provenance = Provenance.MACHINE_OBSERVATION,
        lease_id: str | None = None,
        owner: str | None = None,
        fencing_token: int | None = None,
        event_id: str | None = None,
    ) -> EventEnvelope:
        """构造 Episode 事件；受 fencing 保护的 kind 必须携带 lease 三元组。

        调用方可传预生成 ``event_id``，例如快照行的 ``committed_event_id``，从而
        建立可验证的快照与 journal 关联。
        """

        event_type = EventEnvelope
        resolved_event_id = event_id or f"{kind}.{uuid4().hex}"
        occurred_at = _now_iso()
        return _run_make_episode_event(
            kind,
            episode_id,
            payload,
            event_id=resolved_event_id,
            occurred_at=occurred_at,
            work_item_id=work_item_id,
            task_id=task_id,
            provenance=provenance,
            lease_id=lease_id,
            owner=owner,
            fencing_token=fencing_token,
            policy_version=self._policy_version,
            event_type=event_type,
        )

    def _require_episode(self, snap: Snapshot, episode_id: str) -> EpisodeState:
        ep = snap.episode_by_id(episode_id)
        if ep is None:
            raise EpisodeCommandError(f"unknown episode_id={episode_id!r}")
        return ep

    def _episode_state_to_episode(
        self, state: EpisodeState, ownership_lease_id: str | None = None
    ) -> Episode:
        """把 ``EpisodeState`` 投影为公开 Episode，不暴露审计来源。

        ``ownership_lease_id`` 由调用方从实时 lease 表补入，不从 journal 归约。
        """

        episode_type = Episode
        return _run_project_episode_state(
            state,
            ownership_lease_id,
            episode_type=episode_type,
        )

    def _next_snapshot_version_in_tx(self, episode_id: str) -> int:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM episode_snapshots "
            "WHERE episode_id=?",
            (episode_id,),
        ).fetchone()
        return int(row["v"]) + 1

    def acquire_episode_ownership(
        self,
        episode_id: str,
        *,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None = None,
    ) -> Lease:
        """取得或重新取得 Episode 的 ownership lease。

        返回后续受 fencing 保护写入所需的 token；相同 Episode、幂等键与 owner 的
        重试返回原 lease。
        """

        return self.acquire_lease(
            resource_type=self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
            resource_id=episode_id,
            owner=owner,
            ttl_seconds=ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def release_episode_ownership(self, episode_id: str) -> bool:
        """释放 Episode 的有效 ownership lease。

        实际存在有效 lease 时返回 ``True``。关闭和失败会与生命周期事件一并释放；
        暂停则保留 lease，供后续继续执行。
        """

        assert self._conn is not None
        row = self._read_episode_lease_row(episode_id)
        if row is None:
            return False
        return self.release_lease(row["lease_id"])

    def _grant_episode_ownership_in_tx(
        self,
        episode_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None,
    ) -> Lease:
        """在调用方事务内授予 Episode ownership lease。

        ``start_episode`` 借此原子写入 Episode、lease 与幂等键，不能调用另开事务的
        ``acquire_lease``。本路径递增 fencing 计数器、插入 lease 并记录所有权审计。
        """

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type='episode_ownership' "
                "AND resource_id=? AND idempotency_key=? AND released_at IS NULL",
                (episode_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict("episode_ownership", episode_id)
                # 幂等重试不得返回已过期 lease；TTL 后到达视为冲突，调用方须重新取得。
                if existing["expires_at"] <= now_str:
                    raise LeaseConflict("episode_ownership", episode_id)
                return _lease_from_row(existing)
        token = self._next_fence_token_in_tx(
            self._EPISODE_OWNERSHIP_RESOURCE_TYPE, episode_id
        )
        lease_id = uuid4().hex
        try:
            self._conn.execute(
                "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                "acquired_at, expires_at, idempotency_key, released_at, "
                "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    lease_id,
                    self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
                    episode_id,
                    owner,
                    now_str,
                    expires_str,
                    idempotency_key,
                    token,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # 幂等键占用或并发授权造成的碰撞统一转换为 ``LeaseConflict``。
            raise LeaseConflict("episode_ownership", episode_id) from exc
        self._insert_event_in_tx(
            EventEnvelope(
                event_id=f"episode.ownership_acquired.{uuid4().hex}",
                kind=EventKind.EPISODE_OWNERSHIP_ACQUIRED,
                occurred_at=now_str,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=self._policy_version,
                payload={
                    "lease_id": lease_id,
                    "owner": owner,
                    "fencing_token": token,
                    "expires_at": expires_str,
                },
                episode_id=episode_id,
            )
        )
        return Lease(
            lease_id=lease_id,
            resource_type=self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
            resource_id=episode_id,
            owner=owner,
            acquired_at=now_str,
            expires_at=expires_str,
            idempotency_key=idempotency_key,
            fencing_token=token,
        )

    def start_episode(
        self,
        *,
        work_item_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str,
        task_id: str | None = None,
        previous_snapshot_ref: SnapshotRef | None = None,
    ) -> tuple[Episode, Lease]:
        """原子创建 STARTING Episode 并取得 ownership lease。

        ``previous_snapshot_ref`` 记录接力基线，首次可为 ``None``。绑定 session 并
        受 fencing 保护地转为 ACTIVE 前不允许进度写入，``native_session_id`` 保持
        ``None``。当前没有公开的 STARTING → ACTIVE 命令，后续入口须校验当前 lease
        三元组。幂等重试仅在原 lease 仍有效且 owner 相同时返回原对象；lease 已接管
        或释放时分别抛出 ``LeaseConflict`` 或 ``EpisodeCommandError``。
        """

        assert self._conn is not None
        if not work_item_id:
            raise EpisodeCommandError("work_item_id must be non-empty")
        if not owner or ttl_seconds <= 0:
            raise EpisodeCommandError("owner and positive ttl required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise EpisodeCommandError("idempotency_key must be a non-empty string")
        with self._tx():
            existing = self._conn.execute(
                "SELECT episode_id FROM episode_create_keys WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                episode_id = existing["episode_id"]
                lease_row = self._read_episode_lease_row(episode_id)
                snap = self.replay()
                lease = _lease_from_row(lease_row) if lease_row is not None else None
                if lease is None:
                    raise EpisodeCommandError(
                        f"idempotent replay: episode {episode_id!r} has no active "
                        f"ownership lease (lease expired before retry)"
                    )
                # 幂等重试只能返回同一 owner 的有效 lease；接管后返回新 owner 的 lease
                # 会把写权限错误交给旧调用方。
                if lease.owner != owner:
                    raise LeaseConflict("episode_ownership", episode_id)
                # 已过期 lease 不能写入，也不能暴露为公开 Episode 的 ownership 引用。
                if lease.expires_at <= _now_iso():
                    raise LeaseConflict("episode_ownership", episode_id)
                return (
                    self._episode_state_to_episode(
                        self._require_episode(snap, episode_id), lease.lease_id
                    ),
                    lease,
                )

            episode_id = uuid4().hex
            now = _now_iso()
            # 创建前先验证 WorkItem 与 Task 绑定，避免留下无法安全暂停的 Episode。
            # WorkItem 必须存在；若传入 task_id，它也必须存在并与 WorkItem 一致。
            snap = self.replay()
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == work_item_id),
                None,
            )
            if work_item is None:
                raise EpisodeCommandError(
                    f"work_item_id {work_item_id!r} does not exist; cannot "
                    f"bind an Episode to a missing WorkItem"
                )
            if task_id is not None:
                if work_item.task_id != task_id:
                    raise EpisodeCommandError(
                        f"task_id {task_id!r} does not match WorkItem "
                        f"{work_item_id!r} (work_item.task_id="
                        f"{work_item.task_id!r})"
                    )
                if not any(t.task_id == task_id for t in snap.tasks):
                    raise EpisodeCommandError(f"task_id {task_id!r} does not exist")
            elif work_item.task_id is not None:
                # 带 task_id 的 WorkItem 必须显式绑定同一 Task，否则暂停或激活会错误
                # 进入无 Task 的系统分支。
                raise EpisodeCommandError(
                    f"WorkItem {work_item_id!r} (kind={work_item.kind.value}) "
                    f"is bound to task {work_item.task_id!r}; pass that "
                    f"task_id to bind a Task Episode"
                )
            self._insert_event_in_tx(
                EventEnvelope(
                    event_id=f"episode.create.{episode_id}",
                    kind=EventKind.EPISODE_CREATED,
                    occurred_at=now,
                    source="kernel",
                    provenance=Provenance.MACHINE_OBSERVATION,
                    policy_version=self._policy_version,
                    payload={
                        "episode_id": episode_id,
                        "work_item_id": work_item_id,
                        "task_id": task_id,
                        "status": EpisodeStatus.STARTING.value,
                        "native_session_id": None,
                        "previous_snapshot_ref": (
                            {
                                "episode_id": previous_snapshot_ref.episode_id,
                                "version": previous_snapshot_ref.version,
                                "committed_event_id": previous_snapshot_ref.committed_event_id,
                                "payload_hash": previous_snapshot_ref.payload_hash,
                            }
                            if previous_snapshot_ref
                            else None
                        ),
                    },
                    work_item_id=work_item_id,
                    task_id=task_id,
                    episode_id=episode_id,
                )
            )
            lease = self._grant_episode_ownership_in_tx(
                episode_id, owner, ttl_seconds, idempotency_key
            )
            self._conn.execute(
                "INSERT INTO episode_create_keys (idempotency_key, episode_id, "
                "created_at) VALUES (?, ?, ?)",
                (idempotency_key, episode_id, now),
            )
        snap = self.replay()
        return (
            self._episode_state_to_episode(
                self._require_episode(snap, episode_id), lease.lease_id
            ),
            lease,
        )

    def _fenced_status_change_in_tx(
        self,
        *,
        episode_id: str,
        kind: str,
        new_status: EpisodeStatus,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        extra_payload: dict[str, Any] | None = None,
        work_item_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"new_status": new_status.value}
        if extra_payload:
            payload.update(extra_payload)
        self._append_fenced_event_in_tx(
            self._make_episode_event(
                kind,
                episode_id,
                payload,
                work_item_id=work_item_id,
                task_id=task_id,
                lease_id=expected_lease_id,
                owner=expected_owner,
                fencing_token=expected_token,
            )
        )

    def request_yield(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        reason: str,
    ) -> None:
        """受 fencing 保护地请求 ACTIVE Episode 让出执行权。

        进入 YIELD_REQUESTED 后，可在提交 checkpoint 和关闭前完成在途工具调用。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.ACTIVE:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be ACTIVE to request yield "
                    f"(got {ep.status.value})"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_YIELD_REQUESTED,
                new_status=EpisodeStatus.YIELD_REQUESTED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"reason": reason},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )

    def commit_checkpoint(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        snapshot: EpisodeSnapshot,
        checkpoint_key: str,
    ) -> SnapshotRef:
        """原子提交受 fencing 保护的协作式快照。

        新快照版本与 ``EPISODE_CHECKPOINT_COMMITTED`` 在同一事务落地，Episode 固定
        转为 CHECKPOINTING。相同 ``checkpoint_key`` 的崩溃重试返回原引用，不新增
        版本。快照来源只能是 COOPERATIVE；仅 ACTIVE 或 YIELD_REQUESTED 可走此入口，
        暂停、待调和或恢复中的 Episode 必须使用各自退出命令。
        """

        assert self._conn is not None
        if not isinstance(checkpoint_key, str) or not checkpoint_key.strip():
            raise EpisodeCommandError("checkpoint_key must be non-empty")
        if snapshot.source != SnapshotSource.COOPERATIVE:
            raise EpisodeCommandError(
                "commit_checkpoint is for cooperative snapshots; use "
                "checkpoint_recovery_partial for recovery_partial"
            )
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            # 必须先解析幂等重试再检查状态；若 COMMIT 后响应前崩溃，Episode 已变为
            # CHECKPOINTING，先做状态门禁会破坏 checkpoint_key 的重试契约。
            existing = self._conn.execute(
                "SELECT episode_id, version, payload_hash, committed_event_id "
                "FROM episode_snapshots WHERE checkpoint_key=?",
                (checkpoint_key,),
            ).fetchone()
            if existing is not None:
                # checkpoint_key 全局唯一；命中其他 Episode 属于作用域冲突，不是重试。
                if existing["episode_id"] != episode_id:
                    raise EpisodeCommandError(
                        f"checkpoint_key {checkpoint_key!r} already used by "
                        f"episode {existing['episode_id']!r}; checkpoint_key "
                        f"is globally unique — use a different key"
                    )
                return SnapshotRef(
                    episode_id=episode_id,
                    version=int(existing["version"]),
                    committed_event_id=existing["committed_event_id"],
                    payload_hash=existing["payload_hash"],
                )
            if ep.status not in (
                EpisodeStatus.ACTIVE,
                EpisodeStatus.YIELD_REQUESTED,
            ):
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be ACTIVE or YIELD_REQUESTED "
                    f"to commit a cooperative checkpoint (got {ep.status.value})"
                )
            # ``journal_through_seq`` 是快照覆盖的日志高水位，不能超过当前末尾；虚高
            # 会使下次恢复跳过实际未覆盖的事件，因此拒绝而非截断。
            if snapshot.journal_through_seq > snap.last_seq:
                raise EpisodeCommandError(
                    f"snapshot.journal_through_seq ({snapshot.journal_through_seq}) "
                    f"exceeds the live journal end ({snap.last_seq}); a checkpoint "
                    f"cannot cover events that have not happened yet"
                )
            version = self._next_snapshot_version_in_tx(episode_id)
            payload_text, payload_hash = _payload_json(_snapshot_to_payload(snapshot))
            _validate_episode_snapshot(snapshot, payload_text)
            committed_event_id = (
                f"episode.checkpoint.{episode_id}.{version}.{uuid4().hex}"
            )
            self._conn.execute(
                "INSERT INTO episode_snapshots (episode_id, version, "
                "checkpoint_key, source, payload_json, payload_hash, "
                "base_episode_id, base_version, journal_through_seq, "
                "committed_event_id, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    version,
                    checkpoint_key,
                    snapshot.source.value,
                    payload_text,
                    payload_hash,
                    snapshot.base_snapshot_ref.episode_id
                    if snapshot.base_snapshot_ref
                    else None,
                    snapshot.base_snapshot_ref.version
                    if snapshot.base_snapshot_ref
                    else None,
                    snapshot.journal_through_seq,
                    committed_event_id,
                    _now_iso(),
                ),
            )
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_CHECKPOINT_COMMITTED,
                    episode_id,
                    {
                        "version": version,
                        "source": snapshot.source.value,
                        "payload_hash": payload_hash,
                        "journal_through_seq": snapshot.journal_through_seq,
                        "committed_event_id": committed_event_id,
                        "new_status": EpisodeStatus.CHECKPOINTING.value,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                    event_id=committed_event_id,
                )
            )
            return SnapshotRef(
                episode_id=episode_id,
                version=version,
                committed_event_id=committed_event_id,
                payload_hash=payload_hash,
            )

    def read_episode_snapshot(self, ref: SnapshotRef) -> EpisodeSnapshot:
        """按精确引用读取快照；行缺失、摘要不符或提交事件缺失时均拒绝。"""

        assert self._conn is not None
        row = self._conn.execute(
            "SELECT payload_json, payload_hash, committed_event_id "
            "FROM episode_snapshots "
            "WHERE episode_id=? AND version=?",
            (ref.episode_id, ref.version),
        ).fetchone()
        if row is None:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} not found "
                f"(reducer referenced a row that is absent)"
            )
        if row["payload_hash"] != ref.payload_hash:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} payload_hash mismatch "
                f"(row={row['payload_hash']}, ref={ref.payload_hash})"
            )
        # SnapshotRef、快照行和 journal 事件必须严格一致：committed_event_id 相等，
        # 且事件属于本 Episode。只检查事件存在会放过指向无关 NOTE 的伪造引用。
        committed_event_id = row["committed_event_id"]
        if ref.committed_event_id != committed_event_id:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event_id "
                f"mismatch (ref={ref.committed_event_id!r}, "
                f"row={committed_event_id!r})"
            )
        ev_row = self._conn.execute(
            "SELECT kind, episode_id FROM events WHERE event_id=?",
            (committed_event_id,),
        ).fetchone()
        if ev_row is None:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event "
                f"{committed_event_id!r} not in journal (corruption)"
            )
        if ev_row["episode_id"] != ref.episode_id:
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event "
                f"{committed_event_id!r} belongs to a different episode "
                f"({ev_row['episode_id']!r})"
            )
        if ev_row["kind"] not in (
            EventKind.EPISODE_CHECKPOINT_COMMITTED,
            EventKind.EPISODE_RECONCILE_RESOLVED,
        ):
            raise EpisodeCommandError(
                f"snapshot {ref.episode_id}#{ref.version} committed_event "
                f"{committed_event_id!r} has wrong kind {ev_row['kind']!r} "
                f"(expected checkpoint_committed or reconcile_resolved)"
            )
        return _snapshot_from_payload(json.loads(row["payload_json"]))

    def close_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """受 fencing 保护地把 CHECKPOINTING Episode 关闭为终态。

        正常顺序为 ``request_yield → commit_checkpoint → close_episode``；其他状态
        必须使用各自的退出命令。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.CHECKPOINTING:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be CHECKPOINTING to close "
                    f"(got {ep.status.value}); closing from other states has "
                    f"its own exit command"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_CLOSED,
                new_status=EpisodeStatus.CLOSED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )
            # ownership lease 必须与关闭事件在同一事务释放，避免遗留孤立 lease。
            row = self._read_episode_lease_row(episode_id)
            if row is not None and row["lease_id"] == expected_lease_id:
                self._conn.execute(
                    "UPDATE leases SET released_at=? WHERE lease_id=?",
                    (_now_iso(), expected_lease_id),
                )

    def fail_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        reason: str,
    ) -> None:
        """把 Episode 置为 FAILED，并在同一事务释放相关占用。

        执行层失败后，所属 Task 和 WorkItem 恢复为可重试状态；是否把 Task 置为错误
        终态由 Task 层另行决定。foreground 与 ownership lease 一并释放。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} already terminal ({ep.status.value})"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_FAILED,
                new_status=EpisodeStatus.FAILED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"reason": reason},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )
            now = _now_iso()
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if ep.task_id is not None:
                task = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
                if task is not None and not task.status.is_terminal:
                    if self._read_foreground_task_id() == task.task_id:
                        self._release_foreground_in_tx(task.task_id)
                    if task.status != TaskStatus.READY:
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_STATUS_CHANGED,
                                task.task_id,
                                {"new_status": TaskStatus.READY.value},
                            )
                        )
            if work_item is not None and work_item.status in (
                WorkItemStatus.RUNNING,
                WorkItemStatus.SUSPENDED,
            ):
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.READY,
                        ep.task_id,
                        now,
                    )
                )
            row = self._read_episode_lease_row(episode_id)
            if row is not None and row["lease_id"] == expected_lease_id:
                self._conn.execute(
                    "UPDATE leases SET released_at=? WHERE lease_id=?",
                    (_now_iso(), expected_lease_id),
                )

    def suspend_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        pending: PendingDescriptor,
    ) -> None:
        """原子地把 ACTIVE Episode 转为 SUSPENDED_WAITING_*。

        同一事务写入受 fencing 保护的暂停事件；有 Task 时还将 Task 置为
        WAITING_USER、CAS 释放 foreground，并暂停 WorkItem。系统 WorkItem 跳过
        Task 操作。这里只释放 foreground，外层 Runner 仍须释放资源级 WorkLease。
        """

        assert self._conn is not None
        if pending.kind not in (WaitingSubtype.INPUT, WaitingSubtype.APPROVAL):
            raise EpisodeCommandError(
                f"suspend_episode pending.kind must be INPUT or APPROVAL "
                f"(got {pending.kind.value})"
            )
        if not pending.correlation_id or not pending.cause:
            raise EpisodeCommandError("pending requires correlation_id + cause")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.ACTIVE:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be ACTIVE to suspend "
                    f"(got {ep.status.value})"
                )
            # 在任何写入前校验 WorkItem 及其 Task，避免 Episode 已暂停，Task 却仍
            # 占用 foreground 并保持 RUNNING；任一状态不匹配都回绝整个复合操作。
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if work_item is None:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} bound WorkItem {ep.work_item_id!r} "
                    f"not found; cannot suspend"
                )
            if work_item.status != WorkItemStatus.RUNNING:
                raise EpisodeCommandError(
                    f"WorkItem {ep.work_item_id!r} must be RUNNING to suspend "
                    f"(got {work_item.status.value})"
                )
            task = None
            if ep.task_id is not None:
                task = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} bound task {ep.task_id!r} "
                        f"not found; cannot suspend"
                    )
                if task.status != TaskStatus.RUNNING:
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be RUNNING to suspend "
                        f"(got {task.status.value})"
                    )
            new_status = (
                EpisodeStatus.SUSPENDED_WAITING_INPUT
                if pending.kind == WaitingSubtype.INPUT
                else EpisodeStatus.SUSPENDED_WAITING_APPROVAL
            )
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_SUSPENDED,
                    episode_id,
                    {
                        "new_status": new_status.value,
                        "kind": pending.kind.value,
                        "native_generation": pending.native_generation,
                        "correlation_id": pending.correlation_id,
                        "cause": pending.cause,
                        "posed_at": pending.posed_at,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                )
            )
            # WorkItem → SUSPENDED 只能由一个分支写入。有 Task 时由
            # ``_set_waiting_in_tx`` 同步主 WorkItem、Task 和 foreground；系统
            # WorkItem 在此直接写入，且不涉及 Task 级 foreground。
            if task is not None:
                self._set_waiting_in_tx(
                    task.task_id,
                    WaitingCondition(
                        kind=TaskStatus.WAITING_USER.value,
                        cause=pending.cause,
                        subtype=pending.kind,
                        episode_id=episode_id,
                        correlation_id=pending.correlation_id,
                    ),
                    snap=snap,
                )
            else:
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.SUSPENDED,
                        None,
                        _now_iso(),
                    )
                )

    def resolve_episode_wait(
        self,
        episode_id: str,
        *,
        answer_correlation_id: str,
    ) -> None:
        """收到外部回答后，把 SUSPENDED_WAITING_* 置为 SUSPENDED_READY。

        此处不抢占 foreground；后续 ``activate_suspended_episode`` 抢占成功后再置为
        ACTIVE。该操作由外部回答驱动，且 Episode 已无在途工作，因此不受 ownership
        fencing 约束。
        """

        assert self._conn is not None
        if not answer_correlation_id:
            raise EpisodeCommandError("answer_correlation_id required")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status not in (
                EpisodeStatus.SUSPENDED_WAITING_INPUT,
                EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
            ):
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be suspended_waiting_* to "
                    f"resolve wait (got {ep.status.value})"
                )
            if (
                ep.pending_descriptor is None
                or ep.pending_descriptor.correlation_id != answer_correlation_id
            ):
                raise EpisodeCommandError(
                    f"answer_correlation_id {answer_correlation_id!r} does not "
                    f"match episode {episode_id!r} pending correlation_id"
                )
            # 在任何写入前校验绑定的 WorkItem 和 Task，避免 Episode 已恢复就绪，父对象
            # 却仍处于不兼容状态；任一状态不匹配都回绝整个复合操作。
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if work_item is None:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} bound WorkItem {ep.work_item_id!r} "
                    f"not found; cannot resolve wait"
                )
            if work_item.status != WorkItemStatus.SUSPENDED:
                raise EpisodeCommandError(
                    f"WorkItem {ep.work_item_id!r} must be SUSPENDED to resolve "
                    f"a wait (got {work_item.status.value})"
                )
            task = None
            if ep.task_id is not None:
                task = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} bound task {ep.task_id!r} "
                        f"not found; cannot resolve wait"
                    )
                if task.status != TaskStatus.WAITING_USER:
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be WAITING_USER to resolve "
                        f"a wait (got {task.status.value})"
                    )
            self._insert_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_WAIT_RESOLVED,
                    episode_id,
                    {"answer_correlation_id": answer_correlation_id},
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                )
            )
            now = _now_iso()
            if task is not None:
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        task.task_id,
                        {"new_status": TaskStatus.READY.value},
                    )
                )
            self._insert_event_in_tx(
                self._work_item_status_event(
                    work_item.work_item_id,
                    WorkItemStatus.READY,
                    ep.task_id,
                    now,
                )
            )

    def activate_suspended_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """原子地把 SUSPENDED_READY Episode 激活为 ACTIVE。

        抢占 foreground，将 Task 与 WorkItem 置为 RUNNING，再写入受 fencing 保护的
        激活事件。调用方必须已选择该 Episode；其他 Task 占用时抛出冲突。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.SUSPENDED_READY:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be SUSPENDED_READY to "
                    f"activate (got {ep.status.value})"
                )
            if ep.task_id is not None:
                current_fg = self._read_foreground_task_id()
                if current_fg is not None and current_fg != ep.task_id:
                    raise ForegroundConflict(current_fg)
                task = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} references unknown task {ep.task_id!r}"
                    )
                if task.status != TaskStatus.READY:
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be READY to reactivate "
                        f"(got {task.status.value})"
                    )
                cur = self._conn.execute(
                    "UPDATE foreground_claim SET task_id=? WHERE id=1 "
                    "AND task_id IS NULL",
                    (ep.task_id,),
                )
                if cur.rowcount != 1 and self._read_foreground_task_id() != ep.task_id:
                    raise ForegroundConflict(self._read_foreground_task_id())
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.TASK_STATUS_CHANGED,
                        ep.task_id,
                        {"new_status": TaskStatus.RUNNING.value},
                    )
                )
                if task.primary_work_item_id:
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            task.primary_work_item_id,
                            WorkItemStatus.RUNNING,
                            ep.task_id,
                            _now_iso(),
                        )
                    )
                self._insert_event_in_tx(
                    self._make_task_event(
                        EventKind.FOREGROUND_CLAIMED,
                        ep.task_id,
                        {"task_id": ep.task_id},
                    )
                )
            else:
                # 系统 WorkItem 没有 Task，需在此恢复为 RUNNING，避免 Episode 已
                # ACTIVE 而 WorkItem 仍未运行；foreground 仅属于 Task，无需抢占。
                work_item = next(
                    (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                    None,
                )
                if work_item is not None and work_item.status in (
                    WorkItemStatus.READY,
                    WorkItemStatus.SUSPENDED,
                ):
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            work_item.work_item_id,
                            WorkItemStatus.RUNNING,
                            None,
                            _now_iso(),
                        )
                    )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_ACTIVATED,
                new_status=EpisodeStatus.ACTIVE,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )

    def mark_pending_channel_lost(
        self,
        episode_id: str,
        *,
        reason: ReconcileReason,
    ) -> None:
        """内核检测到待处理通道丢失后进入 RECONCILE_REQUIRED。

        此路径可能发生在 ownership lease 消失后，因此不受 fencing 保护。
        RECONCILE_REQUIRED 是阻塞的非终态，只能由 ``resolve_reconcile`` 退出。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status not in (
                EpisodeStatus.SUSPENDED_WAITING_INPUT,
                EpisodeStatus.SUSPENDED_WAITING_APPROVAL,
                EpisodeStatus.SUSPENDED_READY,
            ):
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be suspended to mark channel "
                    f"lost (got {ep.status.value})"
                )
            self._insert_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_RECONCILE_REQUIRED,
                    episode_id,
                    {
                        "reason": reason.value,
                        "new_status": EpisodeStatus.RECONCILE_REQUIRED.value,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                )
            )
            # 同步阻塞绑定的 Task 和 WorkItem，避免 matcher 或 scheduler 自动恢复。
            # 不能静默跳过状态漂移：resolve 后 scheduler 可能已抢占 Task；非终态 Task
            # 必须进入 reconcile wait，非终态 WorkItem 必须置为 SUSPENDED。
            subtype = (
                WaitingSubtype.REQUIRES_USER_RESTART
                if reason == ReconcileReason.REQUIRES_USER_RESTART
                else WaitingSubtype.RECONCILE
            )
            if ep.task_id is not None:
                task = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
                if task is not None and not task.status.is_terminal:
                    # resolve 与通道丢失标记之间 scheduler 可能已抢占 Task；先释放
                    # foreground，才能把 Task 拉回不可调度的等待态。
                    if (
                        task.status == TaskStatus.RUNNING
                        and self._read_foreground_task_id() == task.task_id
                    ):
                        self._release_foreground_in_tx(task.task_id)
                    wc = task.waiting_condition
                    if task.status == TaskStatus.WAITING_USER and wc is not None:
                        # 原等待仍存在时保留 correlation_id 与 cause，只切换 subtype。
                        waiting_payload = {
                            "kind": TaskStatus.WAITING_USER.value,
                            "cause": wc.cause,
                            "subtype": subtype.value,
                            "episode_id": episode_id,
                            "correlation_id": wc.correlation_id,
                            "deadline": wc.deadline,
                            "condition_kind": wc.condition_kind,
                            "target_ref": wc.target_ref,
                            "match_params": wc.match_params,
                            "open_question": wc.open_question,
                            "preparation_snapshot_ref": wc.preparation_snapshot_ref,
                            "earliest_review_at": wc.earliest_review_at,
                        }
                    else:
                        # 等待已清除或 RUNNING Task 丢失通道时，构造不带
                        # correlation_id 的合成等待原因。
                        waiting_payload = {
                            "kind": TaskStatus.WAITING_USER.value,
                            "cause": (
                                f"pending channel lost ({reason.value}); "
                                f"reconcile required"
                            ),
                            "subtype": subtype.value,
                            "episode_id": episode_id,
                            "correlation_id": None,
                            "deadline": None,
                            "condition_kind": None,
                            "target_ref": None,
                            "match_params": None,
                            "open_question": None,
                            "preparation_snapshot_ref": None,
                            "earliest_review_at": None,
                        }
                    already_correct = (
                        task.status == TaskStatus.WAITING_USER
                        and wc is not None
                        and wc.subtype == subtype
                    )
                    if not already_correct:
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_WAITING_SET,
                                task.task_id,
                                waiting_payload,
                            )
                        )
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            if (
                work_item is not None
                and not work_item.status.is_terminal
                and work_item.status != WorkItemStatus.SUSPENDED
            ):
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.SUSPENDED,
                        ep.task_id,
                        _now_iso(),
                    )
                )

    def resolve_reconcile(
        self,
        episode_id: str,
        *,
        decision: str,
        confirmed_by: str,
        recovery_snapshot: EpisodeSnapshot | None = None,
        recovery_checkpoint_key: str | None = None,
    ) -> None:
        """根据用户或内核确认退出 RECONCILE_REQUIRED，不受 fencing 保护。

        ``close`` 必须留下 recovery_partial 快照；调用方未提供时由最后快照与 journal
        构造。外部 reconcile 决策没有 ownership lease，不能另发受 fencing 保护的
        checkpoint 事件，因此快照身份随本次 resolve 事件记录。``resume_safe`` 把
        Episode 置为 SUSPENDED_READY，并恢复 Task、WorkItem，随后由调用方另行激活。
        事件来源为 ``USER_DECISION``，表示现实状态已经人工确认。
        """

        assert self._conn is not None
        if decision not in ("close", "resume_safe"):
            raise EpisodeCommandError(
                f"decision must be 'close' or 'resume_safe' (got {decision!r})"
            )
        if not confirmed_by:
            raise EpisodeCommandError("confirmed_by required")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.RECONCILE_REQUIRED:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be reconcile_required "
                    f"(got {ep.status.value})"
                )
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            resolve_payload: dict[str, Any] = {
                "decision": decision,
                "confirmed_by": confirmed_by,
            }
            close_lease_row = None  # close 分支读取，用于 release 审计归因。
            if decision == "close":
                snapshot_to_write, ck_key = self._reconcile_close_snapshot(
                    snap, ep, recovery_snapshot, recovery_checkpoint_key
                )
                version = self._next_snapshot_version_in_tx(episode_id)
                payload_text, payload_hash = _payload_json(
                    _snapshot_to_payload(snapshot_to_write)
                )
                _validate_episode_snapshot(snapshot_to_write, payload_text)
                committed_event_id = (
                    f"episode.reconcile_close.{episode_id}.{version}.{uuid4().hex}"
                )
                self._conn.execute(
                    "INSERT INTO episode_snapshots (episode_id, version, "
                    "checkpoint_key, source, payload_json, payload_hash, "
                    "base_episode_id, base_version, journal_through_seq, "
                    "committed_event_id, created_at) VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        episode_id,
                        version,
                        ck_key,
                        snapshot_to_write.source.value,
                        payload_text,
                        payload_hash,
                        snapshot_to_write.base_snapshot_ref.episode_id
                        if snapshot_to_write.base_snapshot_ref
                        else None,
                        snapshot_to_write.base_snapshot_ref.version
                        if snapshot_to_write.base_snapshot_ref
                        else None,
                        snapshot_to_write.journal_through_seq,
                        committed_event_id,
                        _now_iso(),
                    ),
                )
                # 通道丢失会把 Task、WorkItem 留在 reconcile wait 与 SUSPENDED；关闭后
                # 已无命令可修复终态 Episode，因此恢复为 READY，供新 Episode 继续。
                close_now = _now_iso()
                if ep.task_id is not None:
                    close_task = next(
                        (t for t in snap.tasks if t.task_id == ep.task_id), None
                    )
                    if (
                        close_task is not None
                        and not close_task.status.is_terminal
                        and close_task.status != TaskStatus.READY
                    ):
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_STATUS_CHANGED,
                                close_task.task_id,
                                {"new_status": TaskStatus.READY.value},
                            )
                        )
                if work_item is not None and work_item.status in (
                    WorkItemStatus.SUSPENDED,
                    WorkItemStatus.RUNNING,
                ):
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            work_item.work_item_id,
                            WorkItemStatus.READY,
                            ep.task_id,
                            close_now,
                        )
                    )
                resolve_payload["version"] = version
                resolve_payload["source"] = snapshot_to_write.source.value
                resolve_payload["payload_hash"] = payload_hash
                resolve_payload["journal_through_seq"] = (
                    snapshot_to_write.journal_through_seq
                )
                resolve_payload["committed_event_id"] = committed_event_id
                # 记录 close 释放的原 lease 身份，供审计归因。人工决策没有
                # expected_lease 参数，只能在事件 payload 留下该证据。
                close_lease_row = self._read_episode_lease_row(episode_id)
                if close_lease_row is not None:
                    resolve_payload["released_lease_id"] = close_lease_row["lease_id"]
                    resolve_payload["released_lease_owner"] = close_lease_row["owner"]
                else:
                    close_lease_row = None
                resolve_payload["new_status"] = EpisodeStatus.CLOSED.value
                new_status = EpisodeStatus.CLOSED
            else:
                now = _now_iso()
                if ep.task_id is not None:
                    task = next(
                        (t for t in snap.tasks if t.task_id == ep.task_id), None
                    )
                    if (
                        task is not None
                        and not task.status.is_terminal
                        and task.status != TaskStatus.READY
                    ):
                        self._insert_event_in_tx(
                            self._make_task_event(
                                EventKind.TASK_STATUS_CHANGED,
                                task.task_id,
                                {"new_status": TaskStatus.READY.value},
                            )
                        )
                if work_item is not None and work_item.status in (
                    WorkItemStatus.SUSPENDED,
                    WorkItemStatus.RUNNING,
                ):
                    self._insert_event_in_tx(
                        self._work_item_status_event(
                            work_item.work_item_id,
                            WorkItemStatus.READY,
                            ep.task_id,
                            now,
                        )
                    )
                resolve_payload["new_status"] = EpisodeStatus.SUSPENDED_READY.value
                new_status = EpisodeStatus.SUSPENDED_READY
            self._insert_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_RECONCILE_RESOLVED,
                    episode_id,
                    resolve_payload,
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    provenance=Provenance.USER_DECISION,
                    # close 预生成 committed_event_id 以关联快照行；resume_safe 无快照，
                    # 传入 None 生成默认事件 ID。
                    event_id=resolve_payload.get("committed_event_id"),
                )
            )
            if new_status == EpisodeStatus.CLOSED:
                # 释放 close 分支为审计 payload 读取的残留 ownership lease。
                if close_lease_row is not None:
                    self._conn.execute(
                        "UPDATE leases SET released_at=? WHERE lease_id=?",
                        (_now_iso(), close_lease_row["lease_id"]),
                    )

    def _reconcile_close_snapshot(
        self,
        snap: Snapshot,
        ep: EpisodeState,
        recovery_snapshot: EpisodeSnapshot | None,
        recovery_checkpoint_key: str | None,
    ) -> tuple[EpisodeSnapshot, str]:
        """选择 reconcile close 使用的 recovery_partial 快照。

        调用方未提供时，从最后快照和 journal 高水位构造，避免关闭时丢失工作现场。
        返回快照及所用 checkpoint_key。
        """

        if recovery_snapshot is not None:
            if recovery_snapshot.source != SnapshotSource.RECOVERY_PARTIAL:
                raise EpisodeCommandError(
                    "resolve_reconcile close requires a recovery_partial snapshot"
                )
            ck = recovery_checkpoint_key or (
                f"reconcile-close-{ep.episode_id}-{uuid4().hex}"
            )
            return recovery_snapshot, ck

        prev_snapshot = None
        if ep.last_snapshot_ref is not None:
            prev_snapshot = self.read_episode_snapshot(ep.last_snapshot_ref)
        task_goal: str | None = None
        if ep.task_id is not None:
            tstate = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
            task_goal = tstate.original_goal if tstate is not None else None
        built = self.build_recovery_partial(
            work_item_goal=task_goal or f"work_item:{ep.work_item_id}",
            task_constraints_ref=ep.task_id,
            prev=prev_snapshot,
            prev_ref=ep.last_snapshot_ref,
            journal_through_seq=snap.last_seq,
            events=self._collect_recovery_events(
                ep.episode_id, prev_snapshot, snap.last_seq
            ),
        )
        ck = recovery_checkpoint_key or (
            f"reconcile-close-{ep.episode_id}-{uuid4().hex}"
        )
        return built, ck

    @staticmethod
    def build_recovery_partial(
        *,
        work_item_goal: str,
        task_constraints_ref: str | None,
        prev: EpisodeSnapshot | None,
        journal_through_seq: int,
        prev_ref: SnapshotRef | None = None,
        events: tuple[EventEnvelope, ...] = (),
    ) -> EpisodeSnapshot:
        return _run_build_recovery_partial(
            work_item_goal=work_item_goal,
            task_constraints_ref=task_constraints_ref,
            prev=prev,
            journal_through_seq=journal_through_seq,
            prev_ref=prev_ref,
            events=events,
            snapshot_type=EpisodeSnapshot,
            side_effect_type=SideEffectRecord,
            recovery_source=SnapshotSource.RECOVERY_PARTIAL,
            side_effect_event_kind=EventKind.EPISODE_SIDE_EFFECT_RECORDED,
        )

    def _collect_recovery_events(
        self,
        episode_id: str,
        prev_snapshot: EpisodeSnapshot | None,
        high_seq: int,
    ) -> tuple[EventEnvelope, ...]:
        """返回 ``(prev.journal_through_seq, high_seq]`` 内本 Episode 的副作用事件。

        调用方持有事务，``list_events`` 使用同一连接读取。
        """

        from_seq = prev_snapshot.journal_through_seq if prev_snapshot else 0
        out: list[EventEnvelope] = []
        for seq, ev in self.list_events(from_seq=from_seq):
            if seq > high_seq:
                break
            if ev.episode_id != episode_id:
                continue
            if ev.kind != EventKind.EPISODE_SIDE_EFFECT_RECORDED:
                continue
            out.append(ev)
        return tuple(out)

    def checkpoint_recovery_partial(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        reason: str,
        checkpoint_key: str,
    ) -> SnapshotRef:
        """构造并提交受 fencing 保护的 recovery_partial 快照。

        在同一事务固定 journal 高水位、读取前序快照，并提交快照行与 checkpoint
        事件；供超时、关机、硬预算和 ``recover_episode`` 恢复路径使用。
        """

        assert self._conn is not None
        if not isinstance(checkpoint_key, str) or not checkpoint_key.strip():
            raise EpisodeCommandError("checkpoint_key must be non-empty")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} is terminal ({ep.status.value})"
                )
            existing = self._conn.execute(
                "SELECT episode_id, version, payload_hash, committed_event_id "
                "FROM episode_snapshots WHERE checkpoint_key=?",
                (checkpoint_key,),
            ).fetchone()
            if existing is not None:
                # 全局唯一 checkpoint_key 命中其他 Episode 属于作用域冲突，不是重试。
                if existing["episode_id"] != episode_id:
                    raise EpisodeCommandError(
                        f"checkpoint_key {checkpoint_key!r} already used by "
                        f"episode {existing['episode_id']!r}; use a different key"
                    )
                return SnapshotRef(
                    episode_id=episode_id,
                    version=int(existing["version"]),
                    committed_event_id=existing["committed_event_id"],
                    payload_hash=existing["payload_hash"],
                )
            prev_snapshot = None
            if ep.last_snapshot_ref is not None:
                prev_snapshot = self.read_episode_snapshot(ep.last_snapshot_ref)
            task_goal = None
            if ep.task_id is not None:
                task_state = next(
                    (t for t in snap.tasks if t.task_id == ep.task_id), None
                )
                task_goal = task_state.original_goal if task_state is not None else None
            work_item_goal = task_goal or f"work_item:{ep.work_item_id}"
            recovery = self.build_recovery_partial(
                work_item_goal=work_item_goal,
                task_constraints_ref=ep.task_id,
                prev=prev_snapshot,
                prev_ref=ep.last_snapshot_ref,
                journal_through_seq=snap.last_seq,
                events=self._collect_recovery_events(
                    episode_id, prev_snapshot, snap.last_seq
                ),
            )
            version = self._next_snapshot_version_in_tx(episode_id)
            payload_text, payload_hash = _payload_json(_snapshot_to_payload(recovery))
            _validate_episode_snapshot(recovery, payload_text)
            committed_event_id = (
                f"episode.checkpoint.{episode_id}.{version}.{uuid4().hex}"
            )
            self._conn.execute(
                "INSERT INTO episode_snapshots (episode_id, version, "
                "checkpoint_key, source, payload_json, payload_hash, "
                "base_episode_id, base_version, journal_through_seq, "
                "committed_event_id, created_at) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    episode_id,
                    version,
                    checkpoint_key,
                    recovery.source.value,
                    payload_text,
                    payload_hash,
                    recovery.base_snapshot_ref.episode_id
                    if recovery.base_snapshot_ref
                    else None,
                    recovery.base_snapshot_ref.version
                    if recovery.base_snapshot_ref
                    else None,
                    recovery.journal_through_seq,
                    committed_event_id,
                    _now_iso(),
                ),
            )
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_CHECKPOINT_COMMITTED,
                    episode_id,
                    {
                        "version": version,
                        "source": recovery.source.value,
                        "payload_hash": payload_hash,
                        "journal_through_seq": recovery.journal_through_seq,
                        "committed_event_id": committed_event_id,
                        "recovery_reason": reason,
                        "new_status": ep.status.value,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                    event_id=committed_event_id,
                )
            )
            return SnapshotRef(
                episode_id=episode_id,
                version=version,
                committed_event_id=committed_event_id,
                payload_hash=payload_hash,
            )

    def _recover_ownership_in_tx(
        self,
        episode_id: str,
        owner: str,
        ttl_seconds: int,
        idempotency_key: str | None,
    ) -> Lease:
        """在调用方事务内接管 Episode 的 ownership lease。

        活跃 lease 必须已过期或不存在，否则抛出 ``LeaseConflict``。旧行保留并标记
        释放，fencing token 递增后插入新 lease，使新 owner 能在同一事务写入受保护的
        RECOVERING 转换。
        """

        assert self._conn is not None
        now_str = _now_iso()
        expires_str = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        if idempotency_key is not None:
            existing = self._conn.execute(
                "SELECT * FROM leases WHERE resource_type='episode_ownership' "
                "AND resource_id=? AND idempotency_key=? AND released_at IS NULL",
                (episode_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["owner"] != owner:
                    raise LeaseConflict("episode_ownership", episode_id)
                # 不返回已过期 lease；TTL 后到达的重试视为冲突，调用方必须重新申请。
                if existing["expires_at"] <= now_str:
                    raise LeaseConflict("episode_ownership", episode_id)
                return _lease_from_row(existing)
        active = self._conn.execute(
            "SELECT * FROM leases WHERE resource_type='episode_ownership' "
            "AND resource_id=? AND released_at IS NULL",
            (episode_id,),
        ).fetchone()
        if active is not None and active["expires_at"] > now_str:
            raise LeaseConflict("episode_ownership", episode_id)
        if active is not None:
            self._conn.execute(
                "UPDATE leases SET released_at=? WHERE lease_id=?",
                (now_str, active["lease_id"]),
            )
        token = self._next_fence_token_in_tx(
            self._EPISODE_OWNERSHIP_RESOURCE_TYPE, episode_id
        )
        lease_id = uuid4().hex
        try:
            self._conn.execute(
                "INSERT INTO leases (lease_id, resource_type, resource_id, owner, "
                "acquired_at, expires_at, idempotency_key, released_at, "
                "fencing_token) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
                (
                    lease_id,
                    self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
                    episode_id,
                    owner,
                    now_str,
                    expires_str,
                    idempotency_key,
                    token,
                ),
            )
        except sqlite3.IntegrityError as exc:
            # 唯一键占用或并发接管造成的碰撞统一转换为 ``LeaseConflict``。
            raise LeaseConflict("episode_ownership", episode_id) from exc
        self._insert_event_in_tx(
            EventEnvelope(
                event_id=f"episode.ownership_acquired.{uuid4().hex}",
                kind=EventKind.EPISODE_OWNERSHIP_ACQUIRED,
                occurred_at=now_str,
                source="kernel",
                provenance=Provenance.MACHINE_OBSERVATION,
                policy_version=self._policy_version,
                payload={
                    "lease_id": lease_id,
                    "owner": owner,
                    "fencing_token": token,
                    "expires_at": expires_str,
                    "recovery": True,
                },
                episode_id=episode_id,
            )
        )
        return Lease(
            lease_id=lease_id,
            resource_type=self._EPISODE_OWNERSHIP_RESOURCE_TYPE,
            resource_id=episode_id,
            owner=owner,
            acquired_at=now_str,
            expires_at=expires_str,
            idempotency_key=idempotency_key,
            fencing_token=token,
        )

    def recover_episode(
        self,
        episode_id: str,
        *,
        new_owner: str,
        ttl_seconds: int,
        idempotency_key: str,
        reason: str,
    ) -> Lease:
        """接管 ownership lease 已过期的 Episode。

        一个 IMMEDIATE 事务内验证 Episode 非终态，以更高 fencing token 接管 lease，
        再写入 RECOVERING；失败时 lease 一并回滚，避免崩溃留下孤立 lease。调用方随后
        选择提交恢复快照后关闭，或恢复执行。
        """

        assert self._conn is not None
        if not new_owner or ttl_seconds <= 0:
            raise EpisodeCommandError("new_owner and positive ttl required")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise EpisodeCommandError("idempotency_key must be a non-empty string")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} is terminal ({ep.status.value}); "
                    f"cannot recover"
                )
            lease = self._recover_ownership_in_tx(
                episode_id, new_owner, ttl_seconds, idempotency_key
            )
            if ep.status != EpisodeStatus.RECOVERING:
                self._fenced_status_change_in_tx(
                    episode_id=episode_id,
                    kind=EventKind.EPISODE_RECOVERING,
                    new_status=EpisodeStatus.RECOVERING,
                    expected_lease_id=lease.lease_id,
                    expected_owner=new_owner,
                    expected_token=lease.fencing_token,
                    extra_payload={"reason": reason},
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                )
        return lease

    def resume_recovered_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """把 RECOVERING Episode 原地恢复为 ACTIVE。

        调用前必须已提交 recovery_partial 快照，本方法不重复校验该顺序。有 Task 时
        以 CAS 抢占 foreground，并确保 Task、WorkItem 为 RUNNING；系统 WorkItem
        只恢复 WorkItem。崩溃后仍一致的状态保持不变，最后写入受 fencing 保护的转换。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.RECOVERING:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be RECOVERING to resume "
                    f"(got {ep.status.value})"
                )
            work_item = next(
                (w for w in snap.work_items if w.work_item_id == ep.work_item_id),
                None,
            )
            now = _now_iso()
            if ep.task_id is not None:
                task = next((t for t in snap.tasks if t.task_id == ep.task_id), None)
                if task is None:
                    raise EpisodeCommandError(
                        f"episode {episode_id!r} references unknown task {ep.task_id!r}"
                    )
                # 仅允许从 READY 或崩溃后仍存活的 RUNNING 恢复；其他状态必须先显式
                # 解决，不能被无条件改为 RUNNING。
                if task.status not in (
                    TaskStatus.READY,
                    TaskStatus.RUNNING,
                ):
                    raise EpisodeCommandError(
                        f"task {ep.task_id!r} must be READY or RUNNING to resume "
                        f"a recovered Episode (got {task.status.value})"
                    )
                current_fg = self._read_foreground_task_id()
                if current_fg is not None and current_fg != ep.task_id:
                    raise ForegroundConflict(current_fg)
                if current_fg is None:
                    cur = self._conn.execute(
                        "UPDATE foreground_claim SET task_id=? WHERE id=1 "
                        "AND task_id IS NULL",
                        (ep.task_id,),
                    )
                    if (
                        cur.rowcount != 1
                        and self._read_foreground_task_id() != ep.task_id
                    ):
                        raise ForegroundConflict(self._read_foreground_task_id())
                    self._insert_event_in_tx(
                        self._make_task_event(
                            EventKind.FOREGROUND_CLAIMED,
                            ep.task_id,
                            {"task_id": ep.task_id},
                        )
                    )
                if task.status == TaskStatus.READY:
                    self._insert_event_in_tx(
                        self._make_task_event(
                            EventKind.TASK_STATUS_CHANGED,
                            ep.task_id,
                            {"new_status": TaskStatus.RUNNING.value},
                        )
                    )
            if work_item is not None and work_item.status in (
                WorkItemStatus.READY,
                WorkItemStatus.SUSPENDED,
            ):
                # 仅接受暂停激活路径允许的 WorkItem 源状态，不能把任意异常状态改为
                # 最终目标为 RUNNING。
                self._insert_event_in_tx(
                    self._work_item_status_event(
                        work_item.work_item_id,
                        WorkItemStatus.RUNNING,
                        ep.task_id,
                        now,
                    )
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_ACTIVATED,
                new_status=EpisodeStatus.ACTIVE,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"recovery_resume": True},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )

    def close_recovered_episode(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
    ) -> None:
        """把 RECOVERING Episode 置为 CLOSED 终态。

        必须已有 ``last_snapshot_ref``，供下一个 Episode 继续恢复；ownership lease
        在同一事务内释放。
        """

        assert self._conn is not None
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status != EpisodeStatus.RECOVERING:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} must be RECOVERING to close after "
                    f"recovery (got {ep.status.value})"
                )
            if ep.last_snapshot_ref is None:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} has no recovery snapshot; run "
                    f"checkpoint_recovery_partial before close_recovered_episode"
                )
            self._fenced_status_change_in_tx(
                episode_id=episode_id,
                kind=EventKind.EPISODE_CLOSED,
                new_status=EpisodeStatus.CLOSED,
                expected_lease_id=expected_lease_id,
                expected_owner=expected_owner,
                expected_token=expected_token,
                extra_payload={"recovery_close": True},
                work_item_id=ep.work_item_id,
                task_id=ep.task_id,
            )
            row = self._read_episode_lease_row(episode_id)
            if row is not None and row["lease_id"] == expected_lease_id:
                self._conn.execute(
                    "UPDATE leases SET released_at=? WHERE lease_id=?",
                    (_now_iso(), expected_lease_id),
                )

    def record_side_effect(
        self,
        episode_id: str,
        *,
        expected_lease_id: str,
        expected_owner: str,
        expected_token: int,
        action_ref: str,
        idempotency_key: str,
        outcome: str,
        evidence_ref: str | None = None,
        description: str = "",
    ) -> None:
        """记录受 fencing 保护的外部副作用。

        ``done`` 必须提供 ``evidence_ref``；``unknown_requires_reconcile`` 会同时写入
        未确认事件，禁止在核实现实状态前重放。以 ``(action_ref, idempotency_key)``
        幂等，已落盘的崩溃重试不追加重复事件或 UnknownAction。
        """

        assert self._conn is not None
        if outcome not in ("done", "unknown_requires_reconcile"):
            raise EpisodeCommandError(
                f"outcome must be 'done' or 'unknown_requires_reconcile' "
                f"(got {outcome!r})"
            )
        if outcome == "done" and not evidence_ref:
            raise EpisodeCommandError("done side effect requires evidence_ref")
        with self._tx():
            snap = self.replay()
            ep = self._require_episode(snap, episode_id)
            if ep.status.is_terminal:
                raise EpisodeCommandError(
                    f"episode {episode_id!r} is terminal ({ep.status.value})"
                )
            already = self._conn.execute(
                "SELECT 1 FROM events "
                "WHERE kind='episode.side_effect_recorded' AND episode_id=? "
                "AND json_extract(payload, '$.action_ref')=? "
                "AND json_extract(payload, '$.idempotency_key')=?",
                (episode_id, action_ref, idempotency_key),
            ).fetchone()
            if already is not None:
                return
            self._append_fenced_event_in_tx(
                self._make_episode_event(
                    EventKind.EPISODE_SIDE_EFFECT_RECORDED,
                    episode_id,
                    {
                        "action_ref": action_ref,
                        "idempotency_key": idempotency_key,
                        "outcome": outcome,
                        "evidence_ref": evidence_ref,
                    },
                    work_item_id=ep.work_item_id,
                    task_id=ep.task_id,
                    lease_id=expected_lease_id,
                    owner=expected_owner,
                    fencing_token=expected_token,
                )
            )
            if outcome == "unknown_requires_reconcile":
                self._insert_event_in_tx(
                    EventEnvelope(
                        event_id=f"side_effect.unconfirmed.{uuid4().hex}",
                        kind=EventKind.SIDE_EFFECT_UNCONFIRMED,
                        occurred_at=_now_iso(),
                        source="kernel",
                        provenance=Provenance.MACHINE_OBSERVATION,
                        policy_version=self._policy_version,
                        payload={
                            "action_ref": action_ref,
                            "idempotency_key": idempotency_key,
                            "description": description,
                        },
                        work_item_id=ep.work_item_id,
                        task_id=ep.task_id,
                        episode_id=episode_id,
                    )
                )

    def read_snapshot(self) -> Snapshot:
        """返回派生快照，并合并实时 lease 与 foreground。

        schema 版本由 replay 填充；活跃 lease 和 foreground 来自实时表，不由审计
        事件派生。三次读取共享一个 DEFERRED 事务，避免并发 claim 或 release 产生
        割裂快照。
        """

        with self._read_tx():
            snap = self.replay()
            return replace(
                snap,
                active_leases=self._read_active_leases(),
                foreground_task_id=self._read_foreground_task_id(),
            )
