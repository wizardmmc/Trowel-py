# Memory 运行约束

本文件说明当前 memory 读写链路中不能靠目录名或函数签名看出的约束。

## 读取

- session 启动时通过 Claude Code 原生 `--append-system-prompt` 注入稳定上下文，不在 HTTP 反代中改写 memory。
- 注入内容由 core、dictionary L0 和近期日记组成；空 memory 返回空字符串。
- 在线检索通过 stdio MCP。检索、读取和 outcome 都记录 session 身份与引用，不能把空的 native session id 伪造成真实值。
- 桌面模式的在线检索从设置域热读取 `memory_refine` 任务绑定，并把其中可直接调用的
  Anthropic Messages 连接适配为两层 Dictionary 检索 provider；不得读取全局
  `config.toml`。处理器只依赖注入的 Retriever 接口，设置仓储与协议适配停留在 MCP
  composition root。旧 browser/CLI 布局没有应用数据根覆盖时才保留 `config.toml`
  兼容入口。当前绑定不是可直接调用的 Anthropic Messages 连接时明确失败，不换用其他
  任务或全局模型。直接文本调用必须保留冻结连接的认证代理；Memory 检索和 direct API
  后台任务共用同一配置适配器，不能各自降维复制连接字段。
- 新会话只有一个 `memory_enabled` 产品开关，同时控制正文注入与
  `memory.search/read/outcome` MCP；不再暴露独立的 `memory_mcp_enabled` 设置。
- memory/profile 开关在 session 创建时冻结；resume 不能静默改变条件。旧档案仍读取历史
  `memory_mcp_enabled` 以保持原 MCP roster，新会话不再产生两套可独立修改的 Memory 语义。

## 写入与事实源

- 原始经历按 session 写入 `episodes/`；daily 是从 episodes 派生的缓存，删除后应能重建。
- 同一 session 可以产生多个增量 segment，segment id 使用 `<sid>:<start>:<end>`。
- `last_completed_offset` 只能在 CCHost 收到 stdout `result` 边界时记录。持久 jsonl 没有 result 行，不能靠扫描 jsonl 推断完成。
- `last_extracted_offset` 只在对应 segment 全部持久化成功后推进。
- completion manifest 最后写。重试先核验 manifest 声称的产物仍存在，不能只看文件名就判定成功。
- Codex 每个原生 turn 使用独立 normalized JSONL；首事件登记 `(thread_id, turn_id)`，
  terminal 文件 fsync 后才写 completed，memory manifest 完整后才写 extracted。
- daily review 首次领取时，把同一 thread 在 eligible cutoff 前尚未提炼的 completed
  turns 固化为一个 pending fragment。fragment 成员写入 sessions registry，失败重试
  不吸收后来完成的新 turn；manifest 完整后在一个 SQLite 事务中推进全部成员水位。
- Codex journal 中间写失败时用户 turn 继续，但该 turn 保持未完成；terminal 已 fsync、
  DB commit 前崩溃时，daily review 可按匹配的 journal 末行修复水位。

## SQLite 与幂等

- memory sessions registry 使用独立 SQLite，不属于根目录 `trowel.db`。
- SQLite 没有 `ADD COLUMN IF NOT EXISTS`。旧 schema 升级先用 `PRAGMA table_info` 反射，再执行缺失的 `ALTER TABLE`。
- 引用新增列的索引必须在列迁移完成后创建。
- note 幂等由 source session 与 content hash 判断；verification、pain 等可变判断字段不进入内容 hash。

## 来源与派生 provenance

- Memory persist 消费 host-neutral `CompletedSegment`：CC source 是 JSONL byte range，
  Codex source 是 completed turn ids；不能给 Codex 伪造 byte offset。
- `source_models` 只记录来源片段内真实 runtime event 观测；目录名、UI 选择值和默认配置
  不能冒充实测模型。
- `DerivationProvenance` 单独描述执行提炼的 runtime、model/effort、run 与 pipeline version；
  source model 和 generator model 不能共用一个字段。
- model 或 effort 未知时保持未知；已知 effort 不能因为 model 未知而丢失。
- 新 episode/note/meta/manifest 增量写入 provenance；旧 Markdown 缺字段时继续兼容读取，
  episode 仍按 native session id 平铺。
- 当前 Episode 的每条经历是 kind-specific item，不再携带行号引用。daily review
  先在 `daily_review/sources/` 中把来源统一划分为只供理解的 `context` 和本次必须
  处理的 `target`，refine 与 judge 接收同一个 `ReviewSource`，不再分别解释 runtime、
  路径和 offset。Claude Code 的 context 是同一 transcript 的 `[0, start)`，target
  是 `[start, end)`；Codex 的 context 是同一 thread 中位于目标以前且已经提炼的
  turn journals，target 是本次 pending fragment 的 journals。两种 runtime 都只允许
  target 生成 Note、Episode、reflection 和 escalation，成本、activity date、
  provenance、completion manifest 与水位也只按 target 计算或推进。旧 context
  journal 缺失时告警并降级，任一 target 缺失时拒绝提炼。来源均以原路径直接交付，
  不生成拼接文件或来源副本。
  早期 Episode 中已有的 `source_refs` 在读取时忽略，正文和 item 继续兼容恢复。
  离线 repair 也会在严格解析前只移除幸存旧 draft item 的该字段；新 Agent
  Draft 不走这条兼容路径。review 会话启动时只清理自身工作目录里的旧
  `source-*.numbered.jsonl` 生成副本。
- `daily_review/batch.py` 只编排 runtime 和派生物维护；一级目录保留协议和共享
  处理流程，`adapters/claude.py` 与 `adapters/codex.py` 分别把字节区间或 turn
  fragment 适配为同一个 `ReviewUnit`。`processor.py` 统一执行 refine、日期校验、
  持久化、水位推进和 judge。共享接口只使用 `native_session_id`，Codex thread
  不再伪装成带 `cc_session_id` 的 CC 会话记录。
- sessions registry 的 composition root 只暴露 `repo.claude`、`repo.codex` 和
  `repo.review_requests` 三个作用域。CC 的只读待处理查询叫
  `list_pending_segments`；Codex 会写库固化 fragment 成员的领取操作叫
  `claim_pending_fragments`，不能再用看似只读的通用 `find_incremental*` 名字。
- Profile distill 与 Memory daily review 使用彼此独立的处理进度。Profile 从
  sessions registry 只读取得全部已封口 Codex 用户 turns，再扣除
  `profile-distill-state.json` 中自己的 `thread_id + turn_id` 记录；
  `extracted_at`、review fragment、memory/profile 注入开关均不改变该候选范围。
  Claude 字节区间和 Codex 单 turn 都先适配为带 `context/target` 的
  `ProfileDistillSource`，再交给同一 processor、门禁和建议队列。Codex 同 thread
  的较早 turns 只作 context，建议证据必须能在当前 target 的真实用户事件中找到。
  同一 Profile batch 串行处理全部来源；某个 Codex turn 失败时，本轮不越过同
  thread 后续 turns。有建议时先写队列，门禁成功后才写 Profile 处理记录。
- judge 的来源正文已经按同一 ReviewSource 限定 target；其 Python 预提取的
  memory search/read 访问证据仍按原生 `cc_session_id` 汇总。对于同一原生会话的
  多个增量片段，该证据可能包含早期片段的访问记录；这是既有判效证据模型的限制，
  本 slice 不改变评分口径。
- 当前 Episode 提炼偏高召回，不再执行旧的 12 条/1600 字硬压缩。daily 从结构化 item 投影
  outcome、active decision、correction 和 active open loop；evidence、superseded decision
  与 closed open loop 保留在 episode，不进入 daily 进展或待续。
- CC 与 Codex completed segment 的 refine、Profile distill、Daily、Weekly 和 Monthly
  分别解析设置域中的任务绑定，并在每次运行开始时冻结对应运行配置。派生物的 generator
  provenance 写入该快照中的 runtime/model/effort；来源 session 模型仍只来自 runtime
  event/binding，不能和 generator model 混写。
- Weekly v3 消费目标 ISO 周的全部 daily，超 prompt 预算时只按完整 daily 边界分批。
  模型把同一工作主线跨日合并，返回带 `source_days` 的结构化 items；Python 校验来源、
  全部 source-day 覆盖与必需 section，再按 800 字预算整条淘汰。选择器保护日期和
  section 覆盖，以 outcome/decision 为主体；覆盖保底超预算时携带错误重试一次。
  monthly 同样禁止中句截断。
- weekly/monthly 是可重建派生物，frontmatter 保存上游 periods、source hash、generation
  status/version、generated time 与 derivation。上游内容或生成版本变化会标 stale。

## 重生成

- 统一入口是 `plan_regeneration`、`run_regeneration`、`apply_regeneration`；支持
  daily/weekly/monthly 与 missing/failed/stale/all。选择 daily 或 weekly 后会级联其下游。
- plan 只读 live 派生物并保存差异；run 默认只写
  `meta/regeneration/runs/<run-id>/staging/`，失败可续跑且不推进 review watermark。
- apply 只接受 completed run，由人工显式调用，并按 daily -> weekly -> monthly 发布；
  任一 artifact 失败会恢复本次已改 live 文件。v0 调度可自动 plan/run，不自动 apply。
- 重生成只调用 compress，不进入 tidy、retirement、promotion 或 dictionary 链路。
- CLI 使用 `trowel-py memory regenerate` 创建 plan；`--run PLAN_ID` 执行 staging，
  `--apply RUN_ID` 显式发布。

## 测试隔离

- 测试必须把 memory root 指向 `tmp_path`，并注入假的 session registrar；不能注册到真实 `sessions.db`。
- app lifespan 测试也要隔离 memory bootstrap。
- 真实协议 fixture 和真实用户数据不同：可公开 fixture 必须脱敏，带本机路径、正文或额度的录制留在 gitignored 目录。

## 调度

- daily review、profile distill、tidy 由应用生命周期启动，不再依赖旧的 launchd 路线。
- 五项后台任务分别绑定设置域运行配置。每次实际执行前热读取绑定并冻结单次启动快照；
  Claude Code 与 Codex 通过 Agent Host 统一会话边界执行，direct API 只通过已验证协议的
  provider 适配执行。绑定缺失、归档或过期时明确失败，不能回退到全局 Claude 配置。
- 用户显式关闭已结束 turn 的用户会话时，关闭流程先把 Trowel 会话 ID 和 runtime
  持久写入 sessions registry，再删除 Agent binding；CC 请求同时冻结首次绑定和
  关闭时的字节水位，Codex 按 turn 的 Trowel 归属定向领取。应用内 worker 只提炼
  该会话的未处理来源，并在 Episode 提交后用当天全部 Episode 重建一次 Daily。
  跨 Trowel 的旧 Codex fragment、CC 前序区间尚未处理、模型失败或进程锁忙时均
  保留请求，供退避重试、重启 worker 或 nightly 全量扫描补跑。关闭和新会话启动
  都不等待模型。
- daily review 的运行日期与 eligible cutoff 分离；启动 catch-up 和定时 tick 只处理当地
  今天零点以前完成的 CC/Codex segment，今天完成的 turn 留到下一日。
- daily review、profile distill 和 tidy 在 job body 外共用同一 WorkBroker maintenance lease；job 失败、文件锁忙或 tidy 水位未持久化时只释放 claim，不推进 catchup。
- 必要维护按稳定 scheduled period 合并补跑，default tick 不补烧历史预算；长任务按 lease TTL 续租，app 关闭时先 drain worker 再关闭 Broker。
- 当前 Broker usage 的 `calls` 记录获准执行的 maintenance job 次数，墙钟为实测；token 与可见费用仍未知，不能解释成 provider API 调用次数。
