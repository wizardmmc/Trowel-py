# Agent runtime 运行约束

本文件只记录当前仍影响实现的边界。历史来源和修复过程见归档 slice 与 git 历史。

## 项目说明加载

- 2026-08-08 的四格验证只覆盖 macOS 26.5.2 和 Trowel Agent Host 当时的启动装配：
  Claude 使用 Anthropic 兼容的 GLM 连接、`opus` 模型别名、`dontAsk` 权限且无 effort；
  Codex 使用原生连接、`gpt-5.6-sol`、`high` effort 和只读权限。两边均关闭 Memory、
  Profile 与 Self，分别从仓库根和 `trowel_py/discussion/` 启动。结论不得外推到其他
  连接家、模型、权限、上下文开关或启动目录。
- 该矩阵使用 Claude Code 2.1.197 与 Codex CLI 0.144.0 实测：Claude Code 从仓库根
  启动时先加载根 `CLAUDE.md`，访问模块
  源文件时再按 `nested_traversal` 加载模块 `CLAUDE.md`；从模块目录启动时，两层说明都
  在开场加载。
- 同一环境下，Codex 从仓库根启动只注入根 `AGENTS.md`，后续读取模块源文件不会动态
  追加模块 AGENTS；从模块目录启动时，根与模块 AGENTS 在开场合并注入。因此根知识索引
  必须显式链接已发布的模块入口，不能把“读过源码”当成“模块说明已经加载”。
- 根和模块 `CLAUDE.md` 只用 `@AGENTS.md` 桥接共同事实。两种 runtime 不维护内容相近的
  两套项目手册。升级任一 runtime，或修改 Trowel 启动参数、项目上下文来源/桥接、
  连接家策略后，必须按同一四格矩阵重新验证加载边界再修改说明。

## 事件边界

- 前端只消费 Trowel 事件和统一 `AgentEvent` envelope，不直接消费 Claude Code 或 Codex 原始事件。
- Claude Code 原始事件在 `cc_host/translator.py` 翻译；统一封装在 `agent_host` adapter 完成。
- Agent Host 通过 `RuntimeSessionPort` 读取两种 runtime 的连接、未结束轮次、创建
  回滚和关闭能力；容量与删除逻辑不能直接猜测 `CCHost` 或 `CodexSession` 的私有状态。
- runtime 登记、binding 和非用户身份必须作为一个创建事务提交；持久化失败时撤销
  尚未启动的 runtime。删除必须先登记 closing 再检查活动轮次，使新 turn 与 close
  无法交错；只有 runtime close 成功后才能删除 binding。
- live 与 history 必须进入同一套前端 reducer。history 的补偿逻辑不能制造 live 路径没有的语义。
- renderer 只建立一条 `GET /api/agent/events` 应用级 SSE，按 `session_id` 分发全部
  user session 的实时事件；delegate/probe 不进入公开流。旧 per-session SSE 仍保留给
  兼容入口和专属 owner，renderer 不能再为每个会话各开一条连接。
- 应用级 SSE 先发送 `ready` 控制帧，并带连接 generation；空闲时发送 heartbeat。
  慢订阅者使用有界队列，溢出时只为受影响 session 合并 `gap` 控制帧，不能阻塞
  runtime producer 或污染其他 session 的序号。
- 每个 session 独立维护 seq watermark。重复事件丢弃；缺口、重连或 sidecar generation
  改变时进入 `needsReplay`，用 active snapshot 与 history 对账。未知 session 的先到事件
  有界暂存到 materialization 完成，不能静默丢弃，也不能自动重放用户 prompt。
- renderer 启动时必须先等应用 SSE 的 `ready`，再读取第一份 active snapshot；只先创建
  watcher Promise 仍会留下“快照已读、订阅尚未建立”的丢事件窗口。即使 snapshot 中没有
  session，watcher 也必须保持常驻。周期 snapshot 需要合并并发 refresh，等待期间新来的
  refresh 必须补跑；history/Goal 恢复当前最多并发三项，不能重新耗尽 HTTP/1.1 连接池。
- 普通 turn 统一由 `POST /api/agent/sessions/{session_id}/turns` 接受。Claude Code 的
  POST 响应不再承载 renderer SSE；Agent Host 在后台独占并消费原生流，再发布到应用级
  SSE。Codex 继续由原生事件订阅发布。
- 同一 session 不允许并发 send。发送准入读取根 turn 状态，不能用
  `AbortController`、展示 phase 或流是否连通代替业务 running 事实。
- 会话生命周期拆成四类正交状态：resource 表示 runtime/binding 是否仍占资源，turn
  表示根轮次，phase 只描述展示阶段，live 表示实时事实是否连续。active snapshot 携带
  resource、turn、当前 turn ID、状态 generation 和最后事件 seq。
- terminal 只有同时属于当前 session、根 thread 和当前 turn ID 时才能结束根 turn。
  child terminal、旧 turn terminal 或 gap 后 terminal 只触发局部更新或对账；根 terminal
  到达时把缺少结果的运行中工具收敛为“结果事件缺失”，不能继续显示 spinner。

## Claude Code 事实

- 一个 session 对应一个常驻子进程。中断、断连或 stalled 会结束进程，下一次发送用 `--resume` 懒重启。
- `role:user` 的协议消息经常承载 `tool_result`，不等于用户输入。history 必须过滤 meta、command 标签和 local-command 输出。
- `api_retry.attempt` 由 Claude Code 提供，不能在 trowel 内跨请求自行累加。
- AskUserQuestion 通过 `control_request` 到达，回答必须回同一 request 的 `control_response`，并提供 `updatedInput.answers`。
- Agent MCP 的 live guidance 由 Agent Host 应用级 broker 持有；后台 task 独占
  child SSE。stdio MCP 是无状态代理，退出或在下一 turn 重启都不会丢失
  `delegation_id`。父会话关闭和 Agent Host 退出都必须先收敛 broker 中的 child。
- `delegate_start` 创建后台 consumer 后立即返回，`delegate_respond` 在 CC control
  channel 接受答案后立即返回，`delegate_status` 只读当前快照。三个工具都不能
  阻塞到下一次 guidance 或 terminal，也不能另开第二个 SSE reader。
- 父模型可在 start 后继续当前 turn 的独立工作；没有其他有价值的工作时自然结束
  turn。child 继续运行且不占用父模型调用；进入 `needs_guidance`、`completed` 或
  `failed` 新版本后，应用级通知协调器等待父 runtime 空闲，再在同一 Trowel session
  启动内部续轮。运行中的父 turn 不插入消息、不被打断。
- 父会话通知按 `delegation_id/version` 去重，并按父会话串行。空闲检查后若用户 turn
  先启动，通知继续等待下一次空闲。等待和低频实时状态复核都不调用模型；
  `delegate_status` 只用于恢复或显式核对，不能用于运行期间轮询。
- Codex 的 300 秒是 MCP client 默认 deadline，不是协议限制。交互工具快速返回后
  保持默认 deadline，不为 `trowel_agents` 或 Memory MCP 放宽超时。
- 桌面模式的无状态 MCP 客户端必须携带当前实例凭据访问 Agent Host；内部
  delegation 路由不进入公开 OpenAPI，并按父 Trowel session ID 隐藏其他句柄。
- 含实例凭据的会话 MCP 配置必须原子写入并使用 `0600` 权限。内部 start 路由不能
  信任 MCP 客户端提交的 child 创建事实，必须根据当前父 binding 复核目录、权限、
  上下文继承、delegate 身份、Memory 资格和禁止递归约束。
- Agent Host 创建 delegation 前必须重新确认父 binding；父关闭边界先登记 guard
  再清理已有 child，随后到达的 start 必须拒绝，不能留下无 owner child。
- Agent MCP 的服务名、启动模块、工具列表和父会话环境变量由
  `agent_mcp/launch.py` 统一定义；Claude Code 与 Codex 只负责转换成各自的 MCP
  配置格式。
- Agent MCP 不再读取全局 Claude/Codex 配置，也不接收临时 model 或 effort。设置域把
  `runtime + 模型连接 + model + effort` 保存为运行配置；新父会话只冻结当前
  `agent_callable` 配置的稳定别名和启动事实。设置修改只影响之后新建的父会话，既有父会话
  继续使用自己的冻结表。
- 稳定别名允许修改。旧别名永久保留、不能分配给其他配置，也不能进入新父会话；已经冻结
  旧别名的父会话仍可继续使用。固定五个 MCP 工具只接受运行配置别名与任务，不按配置动态
  增删工具。
- Claude Code 为 Agent MCP 的完整工具名传入 `--allowedTools`。这是当前 GLM 首轮
  能通过 ToolSearch 发现委派工具的必要启动条件；工具同时携带
  `anthropic/alwaysLoad=true`，供支持 MCP 工具直载的 Claude Code 版本使用。
- AskUserQuestion answer key 在 MCP 边界接受完整 `question` 或唯一 `header`，写回
  CC 前统一转成完整 question；未知、缺失、重复和歧义 key 必须拒绝，HTTP 写入成功
  本身不证明 child 语义上取得了答案。
- interactive close 遇到仍在创建的 child 时，必须先有界等待 binding id，再按
  interrupt/delete 顺序清理。id 尚未知时保留 `unknown_requires_reconcile`；Agent
  Host 可继续等后台 create 回写后重试，不能把未知创建结果当作已删除。
- 当前交互协议的 child 只发布 Claude Code；CC/Codex 都可作为父会话调用它。
  Codex child 的任意提问协议没有生产 fixture，不能把原生 approval 通道冒充为
  对称的多轮 guidance。
- interactive delegation handle 在当前 Agent Host 应用进程内有效，可跨父 turn 和
  stdio MCP 进程重启读取。Agent Host 整体重启后的 durable record/reconciliation
  尚未实现，不能从 task 文本或 child binding 猜测后自动重放。
- Claude Code 与 Codex 的消息轮和内部续轮都进入同一常驻 Agent 事件订阅。订阅只
  推送连接期间的实时事件；断线期间的内容由原生历史恢复。
- Model OS 托管会话单独挂载 `trowel_model_os.yield`，不复用 `trowel_agents`。
  `model_os_mcp_enabled` 默认关闭并随原生 session 冻结；tool 只登记 proposal，
  不能把 MCP response 当 turn terminal。旧 `/api/cc` 创建路径不得开启该工具，
  因为它没有 Agent Hub binding 与 Episode owner。
- 强制 interrupt 必须先校验 expected turn 与 process/connection generation；Codex
  ACK 后继续等 native terminal，CC 的 `error_during_execution` 只有与 command 同代
  关联时才归一化。正常 `finished` 后的 `session_exited` 不能重新解释成 host loss。
- EnterPlanMode 与 ExitPlanMode 在 print 模式下仍会发出 `can_use_tool` control request。
  Host 必须把它转换成前端可回答的确认请求；批准时回传原始 input，拒绝时回传 deny。
  不能静默丢弃或自动批准，否则前者会让 CC 一直等待，后者会绕过计划审批语义。
- session registry 在进程内，部署只支持单 worker。

## 请求预算

- Codex 模型目录是可安全重试的 GET。Agent route 对完整目录读取使用 25 秒总预算；超时
  返回 HTTP 504、稳定错误码 `request_timeout`、操作名 `codex_model_catalog` 和毫秒预算。
- renderer 对同一请求使用 30 秒预算，让后端有 5 秒时间先返回可分类错误。两个边界不能
  使用相同 deadline，避免在截止点重新发生 transport 取消与后端响应竞速。
- `CodexHostManager` 的 60 秒协议请求预算继续服务 thread、turn、history 和 catalog
  单页等原生调用，不能为了模型目录缩短。route 的总预算负责取消目录等待和清理 pending。
- renderer 从发起请求时开始计算预算：普通 GET/history 为 15 秒，active snapshot 为
  10 秒，live readiness 为 10 秒，创建/turn-start/close 为 30 秒，interrupt 为 15 秒。
  sidecar 的 turn-start/close 为 25 秒，interrupt 为 10 秒，保证 renderer 取消前仍有
  时间收到结构化结果。
- 超时和断连使用结构化问题码。`turn_acceptance_unknown` 不提供普通重试，避免重复创建
  turn；`turn_state_unknown` 进入 snapshot 对账；`close_needs_reconcile` 保留会话行并
  提供重试关闭和脱敏诊断。只有后端确认 `closed` 后才能从多开栏删除会话。
- 新建 session 使用 `X-Trowel-Request-Id` 做幂等接受。renderer 对同一代新建操作复用
  request ID；请求结果未知时先按该 ID 查询既有结果，不能自动创建第二个 runtime。

## 磁盘与恢复

- `resource-lifecycle.json` 是带进程终止权限的易失运行快照，不是可迁移业务数据。
  v2 快照同时绑定应用实例和当前数据根的去敏身份；Python reaper 与 Electron Host
  在发信号或核验资源前都必须验证版本、实例和数据根。复制到另一目录的快照必须按
  不可用处理，不能继承原目录的 PID/进程组所有权。
- 真实 runtime 测试若需要业务配置副本，必须使用
  `python -m trowel_py.desktop.isolated_data_copy`：SQLite 走 backup API，资源快照、
  退出标记、锁、WAL/SHM、临时文件和符号链接一律排除。目标必须为空且位于源目录外；
  不能用 `cp`/`copytree` 整体复制正在运行的 Desktop 数据根。
- Electron Host 在 sidecar 首次 ready 后串行执行有超时的 readiness 探针。连续三次
  身份或连接检查失败时进入 `readiness_lost` 诊断，并把退出记录为
  `sidecar_abnormal` 后收敛仍存活的旧进程；renderer 写操作不自动重放。最终核验为
  `closed` 才能从诊断页重试；`needs_reconcile` 时切换为 `reconcile_required`，保留旧
  快照并阻止同一实例启动新 sidecar，用户必须退出后以新实例重启。只读 inspection
  sidecar 的空快照也必须使用同一 v2 数据根身份契约。异常退出标记只由统一 shutdown
  链路写入；健康检查与用户重试并发时共享同一收敛结果和幂等诊断切换。readiness
  超时或 renderer 加载失败发生在启动阶段时，cleanup 终态也必须传回 Host；只要为
  `needs_reconcile`，Retry 和直接 Start 都不得覆盖旧快照。
- Claude Code 会话同时包含 `<session>.jsonl` 和同名目录；workflow、subagent 与 tool result 位于同名目录中。
- workflow 进度不从 stdout 推送。`WorkflowWatcher` 轮询 `workflows/wf_<runId>.json`，该文件是整棵树的事实源。
- subagent usage 从 transcript 的 assistant usage 累加，不能依赖 GLM 下经常为 0 的 task progress usage。
- checkpoint 写入私有 `refs/trowel-checkpoints/`，不移动 HEAD。回滚只恢复 worktree/index 并截断 jsonl，不重写用户提交历史。
- Trowel 托管的 Codex live event 在 `CodexSession` 边界写入 per-turn normalized journal；
  前端断开不影响 journal，terminal 文件 fsync 后才推进 memory completed 水位。
- Codex `thread/read` 缺少 commandExecution。主会话 history 按 turn 优先使用已
  封口的 normalized journal，缺失、尚未封口或损坏时才回退到 `thread/read`；
  原生快照不能覆盖 journal 正文。memory-off 关闭记忆注入和检索，不删除会后
  经历或供界面回放使用的 journal。

## 本地反代

- CC 请求经本地反代进入 Anthropic 兼容服务。反代会删除污染缓存 key 的 billing system block，并替换交互身份块。
- 反代必须透传流，不做响应缓冲和双层重试。
- 设置域连接会话使用随机不透明租约路径绑定冻结上游。租约表不保存凭据；连接凭据和
  角色模型只写入该 Claude 子进程使用的 `0600` 私有 settings，关闭会话时同时释放
  settings 与租约。桌面 Bearer 中间件不拦截这条内部路径，Claude 子进程以租约令牌
  鉴权；其他 `/api` 仍要求实例 Bearer。未提供或失效的连接不能回退到全局 Claude 配置。
- `api_retry` 只出现在 stream-json stdout，不写入 jsonl；诊断重试要看运行日志。
- dev 与 stable 是两份代码。dev 验证通过后由人同步，不能直接修改 stable。

## 连接级 Runtime pool

- 普通 Agent 一旦存在设置域 runtime 连接，就必须显式选择 connection、model 和
  effort；Agent 默认只引用一份运行配置，不从最近使用、第一项或全局 CLI 配置兜底。
  后台任务也逐项引用运行配置，每次任务开始时热读取并冻结本次启动快照；设置保存后下一次
  会话或任务立即生效，不要求重启应用。Claude 交互模型使用已配置主会话角色
  别名。Codex 的原生 `model/list` 只提供候选兼容性与 effort 元数据，设置域保存用户
  明确选择的有序模型白名单；新会话只展示该白名单并保持保存顺序。API 校验连接、
  runtime 与已保存 catalog，具体交互兼容性由原生 runtime 创建响应裁决；少量 smoke
  记录只决定后台任务资格，不禁用普通交互模型。每次打开新会话配置都重新读取设置，
  新增供应商或修改模型不要求重启应用；已运行会话继续使用冻结事实。
- 新建 Claude 兼容会话使用连接级独立 `CLAUDE_CONFIG_DIR`，并保留每会话 `0600`
  `--settings` 覆盖层与 `--strict-mcp-config`。启动链路不传空的 `--setting-sources`；该
  isolation mode 会连带屏蔽连接家中的 `CLAUDE.md`、rules 和 skills。Trowel MCP roster
  仍只来自每会话 composite 配置，连接家的用户/项目 MCP 不因此开放。
- Codex 每项冻结连接由独立 `CodexHostManager` 持有。启动参数用 app-server 后的
  `-c key=value` 覆盖，第三方凭据只进入对应 manager 环境；不得使用 CLI 不支持的
  `--profile`，也不得改写用户全局 Codex 配置。
- 新建 Codex Official/Custom 会话统一冻结连接级 `CODEX_HOME`。app-server 进程的
  `HOME` 同时指向连接家，以隔离 Codex 固定扫描的 `.agents/skills`；shell 工具通过
  `shell_environment_policy.set.HOME` 恢复真实用户家。`CODEX_SQLITE_HOME` 继续指向
  共享 thread/state 根。旧冻结档案没有连接家字段时继续采用修复前目录，不能静默迁移。
- Codex 全局配置复制是用户显式触发的一次性覆盖快照：只复制 `config.toml`、
  `AGENTS.md`、`rules`、`~/.codex/skills` 与 `~/.agents/skills`，不复制认证、会话、
  SQLite、日志或插件缓存；各连接复制后独立演化，不做实时同步。
- Codex 配置覆盖和连接删除先按 connection ID 独占该连接全部新旧 identity manager。
  任一 identity 仍有活动会话时返回 409；无引用的 manager 全部关闭后才组装并交换完整
  配置家，操作结束再解除门禁。这样重新复制后的下一次启动必建新 app-server，不复用
  已加载旧 `config.toml` 的 manager。目录交换中断时保留旧家 recovery backup，下一次
  解析连接家先恢复或清理已提交事务，不能删除唯一可恢复副本。manager close 结果未知时
  保留 manager 与连接维护门禁，后续显式维护重试关闭；不能忘掉旧进程后启动第二个实例。
- Agent 输入区的 `/` 是 Trowel 已适配的 Codex 会话命令，`$` 才是 Codex 技能。
  技能目录必须由当前 session 所属 manager 调用原生 `skills/list`，workdir 从 binding
  读取；HTTP 只返回脱敏名称、说明、scope 和 enabled，不暴露本机路径。
- pool key 包含连接 ID、身份版本、认证目录/代理和启动配置摘要；model/effort 属于
  session 条件，不创建额外 manager。多个 manager 共用持久 thread/state 根，空 state
  的首次初始化必须先由共享单锁串行预热。
- manager 按连接 identity 惰性创建并常驻。打开 Agent 页面、列出供应商或组装新会话
  选项不能触发全部连接的 `model/list`；只有显式刷新模型、Official 账号操作、创建或
  恢复该连接会话时才允许启动对应 manager。容量 25 约束同时存在的 manager identity，
  同一连接的新会话复用 manager，不按新建次数累计。
- Codex 历史不能从“本次进程已经启动了哪些连接 manager”推断。专用历史 manager
  固定连接共享 `CODEX_SQLITE_HOME`，用 `thread/list` 的 `useStateDbOnly=true` 读取
  全部多连接 thread；旧兼容 manager 只补充多连接改造前留在默认 `CODEX_HOME` 的
  thread。两者都不属于供应商连接 manager，历史查询不得破坏连接懒启动。统一非用户
  原生 ID 必须透传到两个来源，并在各自原生分页截断前排除。
- Codex Official 的一个供应商对应一个账号槽位。槽位底层是隔离的 `CODEX_HOME`，由
  Trowel 自动分配并隐藏；登录和 refresh 走 app-server 原生账号接口，OAuth 文件仍由
  Codex 写入。设置域只保存槽位引用、脱敏邮箱/套餐摘要和可选代理，不复制 token；
  账号摘要不得进入普通日志或 telemetry。
- manager 保持常驻到应用退出。共享状态历史源失败必须向上传播；旧兼容历史源失败时
  保留共享状态结果。应用退出聚合关闭历史读取器和全部 manager，不能因首个异常跳过
  后续资源。
- 原生 session/thread ID 建立后，runtime、连接、model、effort、permission、
  Memory/Profile/Self 和 Agent MCP 条件写入脱敏私有档案。binding 删除后恢复仍读取该
  档案；连接身份版本变化时要求手动重绑，不能拿当前默认连接覆盖。
- 新用户会话只要存在至少一份可用、明确开放 Agent 调用且带稳定别名的运行配置，就自动
  挂载 Agent MCP；没有可调用目标时不挂载。`agent_mcp_enabled` 只作为旧档案恢复字段，
  不再是用户设置或公开创建参数。delegate 与 background 会话禁止递归挂载。
- Agent MCP 的 `tools/list` 从父会话 binding 读取创建时冻结的调用目标：阻塞委派的
  `configuration` 枚举展示全部稳定别名，交互委派只展示 Claude Code 别名。别名错误时
  MCP 参数校验或服务端错误必须同时返回可用清单，模型不应猜测 runtime 名称。
- 新会话的 `memory_enabled` 同时控制 Memory 正文注入和 Memory MCP。旧档案仍按创建时
  保存的 MCP roster 恢复，不能用当前设置覆盖历史会话。
- Claude 连接保存 `claude_auto_memory_disabled`，启动时写入连接私有 settings 顶层的
  `autoMemoryEnabled: false`。该字段只影响之后创建的 Claude 会话，恢复旧会话沿用档案中
  的冻结值；它只关闭 Claude 原生 auto-memory，不影响 Trowel Memory 正文或 MCP。

## 测试

- translator fixture 优先来自真实录制。
- 真 CC 集成测试默认排除：

```bash
CC_INTEGRATION=1 .venv/bin/python -m pytest -m integration tests/cc_host/test_integration.py
```

- 公开 event type、OpenAPI 和 CLI 由 `tests/contracts/` 保护。修改事件词表时必须同时检查 adapter、history、前端类型和 reducer。
- `cd web && bun run desktop:agent-transport-smoke` 通过 Electron renderer 和 Chromium
  HTTP/1.1 网络栈验证 20 connected、5 running、单条应用 SSE、容量拒绝、并发读取和
  资源归零。package 复验使用 `smoke-packaged-app.mjs --agent-transport`，并注入隔离的
  可控 Claude runtime；不得访问真实 provider 或覆盖正在运行的 App。
