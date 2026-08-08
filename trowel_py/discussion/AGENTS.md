# Discussion 后端领域上下文

本文件只覆盖 `trowel_py/discussion/` 的 Python 后端。前端 `web/src/discussion/` 拥有
自己的状态和展示边界；跨端只通过 Trowel DTO、SSE 和共享 reference 对接。

## 领域职责与 owner

- `routes.py` 只拥有 HTTP/SSE 适配、依赖取得、validation/domain 错误映射和 Discussion
  隐私脱敏 fallback；通用 envelope 政策仍由应用全局异常处理拥有。业务状态由 service、
  coordinator 和 repository 决定。
- `service.py` 拥有创建、用户命令与幂等协议编排、文件先行写入和 Discussion 聚合公开 DTO；
  不直接管理 Claude Code 或 Codex 进程。`timeline.py` 另行拥有 attempt 原生历史的公开 DTO。
- `coordinator.py` 拥有 participant 会话认领、物理并发、attempt 消费、同轮共同发布、
  启动恢复、停止和资源收敛。
- `repository.py` 拥有 SQLite 聚合、命令收据持久化、事务内 expected-version CAS、durable
  状态事件和唯一发布；`artifacts.py` 拥有 durable 正文证据。SQLite 还保存顶层用户消息
  正文副本供 Profile 消费，读取前必须与 artifact 的相对路径、哈希和字节数对账。
- `participant_sessions.py` 是 participant runtime 到 `agent_host` 的会话适配边界；runtime、
  连接、模型、权限和能力在创建时冻结，恢复时不得静默换成当前设置。`handoff.py` 另行
  适配轮次间或研讨收口后创建的普通 Agent 会话。
- `state_machine.py` 只计算轮次是否可发布以及发布后的确定性状态；`episode.py`、
  `handoff.py` 分别拥有聚合 Episode 和普通 Agent 交接的确定性投影。
- `events.py` 拥有进程内唤醒和 live attempt 事件，不替代 repository 的 durable 事件。

## 不可破坏的不变量

### 同轮快照与共同发布

- 一轮的 prompt 在创建 round 时冻结。参与者即使因最多五路物理并发而分批启动，也必须
  收到逐字相同的同轮输入；下一轮只能读取经过哈希校验的已公开结果。
- participant 的实时工作过程和回复增量可以提前观察；最终回答在整轮所有槽位进入明确
  终态前不能进入 Discussion 聚合快照、publication、transcript 或 Episode。失败、限额、
  超时和 host loss 是可发布终态，不得让单个失败永久卡住其他参与者。
- `DiscussionCoordinator` 先生成 durable publication manifest，
  `DiscussionRepository.publish_round` 再以一个 SQLite 事务唯一发布整轮、推进 discussion
  状态并追加事件。没有 manifest 或完整性校验失败时不能从槽位文件拼出半轮结果。

### 幂等、恢复与完整性

- 继承 `VersionedCommand` 的生命周期、消息、标记和交接命令由
  `command_id + 请求哈希` 去重，并用 expected version 防止并发覆盖。同一 command ID
  携带不同请求必须冲突，不能以最后写入者覆盖。
- participant question answer 只是当前 attempt 的实时交互：coordinator 在进程内保存有界
  回答哈希，使同一答案重复提交幂等；收据不持久化，也没有 expected-version CAS，不能
  外推为进程重启后仍可重用。
- 应用启动恢复的是原 discussion 的同一逻辑 round，不是重放旧 attempt。旧运行 attempt
  先封口为 `host_lost`；已成功槽位不重跑，未完成槽位创建新 attempt。旧 attempt 的
  partial 或 final output 不得作为槽位最终结果进入共同发布、transcript 或 Episode；其实时
  与历史 attempt 轨迹仍可按审计边界观察。回答已经封口但 publication 文件失败时，恢复应
  复用结果而不是再次调用模型。
- artifact 必须先 durable，再在 SQLite 中登记相对路径、SHA-256 和字节数。公开 DTO、
  coordinator 和 Profile 的验真路径会在正文漂移、缺失或越出数据根时登记完整性 outbox；
  transcript 是可重建缓存，不是事实源。`get_transcript` 与 handoff 重建失败当前直接报错，
  不得外推为所有 artifact 读取失败都会留下 outbox。

### 停止、删除与资源收敛

- stop 先持久化停止事实，再中断活动 turn、等待 worker 退出并按稳定 `owner_ref` 关闭每个
  participant binding。中断 ACK 不是终态；关闭结果未知时 participant 保持
  `needs_reconcile`，由后续 stop、delete 或应用启动继续对账。
- delete 只接受 completed 或 stopped 研讨。所有 participant 资源必须确认关闭，聚合
  Episode 也必须 durable，之后才能 soft delete；任一条件未知都必须拒绝删除。
- soft delete 隐藏 Discussion，但保留 tombstone 和聚合 Episode provenance。不得为了
  删除界面记录而删除仍需恢复的 runtime、未公开 artifact 或 Episode 来源。

## Agent runtime、Memory、Profile 与 telemetry 边界

- participant 以 `session_kind="discussion"` 进入 Agent Host，`memory_eligibility=False`、
  Agent MCP 关闭且禁止递归委派；其冻结的 Memory/Profile/Self 开关只控制该会话可用的
  上下文能力，不能把 participant 原生会话变成普通用户会话来源。
- completed 或 stopped Discussion 只生成一条宿主级聚合 Episode。Episode 固定记录议题、
  Discussion 状态、完整记录引用，以及 participant 名称、runtime 和 model 摘要；逐轮写入
  已共同发布的 participant 原话或失败状态，未公开轮只写最终状态而不写封闭回答。它不
  调用主控模型另行总结，也不直接生成 Note。
- Profile 只消费 `discussion_user_messages` 中的顶层用户原话，并维护独立水位；participant
  回答、模型互相赞同和交接内容不能用于反推用户偏好。
- telemetry 当前只有通用 Agent/runtime/命令级信号，没有 Discussion 专属操作，也不能
  证明 Python 函数或模块级生产可达性。

## 运行事实状态

| 状态 | 当前结论与证据边界 |
|---|---|
| `verified_live` | record=absent; checked=[PR #113](https://github.com/wizardmmc/Trowel-py/pull/113) 的真实 isolated 双 runtime 多轮、历史恢复和 Agent 交接; gap=未保存完整连接、model、effort、权限及 Memory/Profile/Self 配置; do_not_claim=可复现的 verified_live 基线; exit=按完整配置矩阵重跑并保存去敏证据 |
| `compatibility_required` | record=present; contract=已成功槽位不重跑、未完成槽位新增 attempt、旧 partial 不进入共同发布; commit=33f3e1e; tests=tests/discussion/test_terminal_and_recovery.py |
| `unknown` | record=present; checked=截至 2026-08-08 的正式数据和通用 telemetry; gap=没有 retry attempt、Discussion 专属恢复信号或应用进程中断后的同轮恢复实跑; do_not_claim=页面刷新历史恢复和 Agent 交接已证明 verified_live; exit=补做产品链进程中断后的同轮恢复实跑 |

当前没有经过三类证据和人审的 `removal_candidate`。未观测到不能推导为死代码或删除授权。

## 权威测试入口

- 同轮快照、发布屏障和物理分批：
  `.venv/bin/python -m pytest tests/discussion/test_round_coordination.py`
- attempt 终态、同轮恢复、旧 partial 隔离和 publication 哈希对账：
  `.venv/bin/python -m pytest tests/discussion/test_terminal_and_recovery.py`
- stop、delete、Episode outbox 与资源关闭：
  `.venv/bin/python -m pytest tests/discussion/test_lifecycle.py`
- Episode、Note 与 Profile 三条资格：
  `.venv/bin/python -m pytest tests/discussion/test_memory_boundaries.py`
- 并发命令、请求哈希和用户消息正文副本一致性：
  `.venv/bin/python -m pytest tests/discussion/test_idempotency.py`
- artifact 路径、字节和符号链接边界：
  `.venv/bin/python -m pytest tests/discussion/test_artifacts.py`
- route 错误脱敏、公开 envelope 和 attempt timeline DTO：
  `.venv/bin/python -m pytest tests/discussion/test_routes.py`
- participant 冻结配置和 `owner_ref` 恢复：
  `.venv/bin/python -m pytest tests/discussion/test_participant_sessions.py`
- 普通 Agent 交接上下文、幂等创建和恢复：
  `.venv/bin/python -m pytest tests/discussion/test_handoff.py`

## 继续阅读

- [Agent runtime 运行约束](../../docs/reference/agent-runtime.md)
- [Memory 运行约束](../../docs/reference/memory-runtime.md)
- [开发流程与项目上下文规则](../../docs/foundation/development.md)
