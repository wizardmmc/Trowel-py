# 仓库地图

这是一张源码导航图，不展开实现历史，也不逐文件复制目录树。

## 根目录

| 路径 | 职责 |
|---|---|
| `README.md` | 产品介绍与运行方式 |
| `pyproject.toml` / `uv.lock` | Python 包、依赖和测试配置 |
| `config.example.toml` | 不含真实凭据的配置样例 |
| `.github/` | CI workflow、Issue/PR 模板、CODEOWNERS 与 dependabot 配置 |
| `trowel_py/` | FastAPI 后端与本地运行时 |
| `web/` | React 前端 |
| `tests/` | Python 测试与公开契约快照 |

本地设计文档、agent 指令、真实配置、数据库、日志和录制数据均被 gitignore，不属于公开仓库内容。

## 后端

| 路径 | 职责 |
|---|---|
| `app.py` | 组装 FastAPI、生命周期与路由 |
| `cli.py` | `trowel-py` 命令行入口 |
| `application_paths.py` | 统一解析 Trowel 自有数据库、Memory、配置与本地索引的数据根目录 |
| `desktop/` | Electron Host 使用的 sidecar 启动、实例认证、版本握手与 readiness |
| `desktop/packaged_entrypoint.py` | 冻结可执行文件的白名单分发入口，只启动 sidecar、Agent MCP 或 Memory MCP |
| `desktop/data_migration.py` | 离线盘点并原子迁移旧 Memory/Profile、当前候选 journal 与本地索引，不导入旧 Garden |
| `desktop/data_root_lock.py` / `desktop/data_compatibility.py` | 独占长期数据根，并阻止 dev 抢先执行正式 App 尚未应用的 schema migration |
| `config.py` | 模型服务配置读取 |
| `db/` | 主数据库连接与 SQL 迁移 |
| `agent_host/` | Claude Code 与 Codex 的统一会话边界 |
| `agent_host/capabilities.py` | 版本化保存两种 runtime 已实证可用的公开能力矩阵 |
| `agent_host/runtimes/` | 两种 runtime 的共同实时状态、创建回滚、关闭操作和对称事件适配器 |
| `agent_host/capacity.py` | 跨 runtime 的用户连接与委派连接/在跑容量裁决 |
| `agent_host/lifecycle.py` | runtime 登记、binding、非用户身份和关闭标记的一致提交与回滚 |
| `agent_host/delegate_identity.py` | 兼容旧文件名并持久记录所有非用户原生会话 ID，供历史扫描在分页前排除内部会话 |
| `agent_mcp/` | 跨 runtime MCP 工具、blocking delegation 与进程内 live guidance 生命周期 |
| `agent_mcp/launch.py` | Agent MCP 的服务名、启动命令、工具列表和父会话环境规格 |
| `agent_host/events.py` | 两种 runtime 共用的 AgentEvent wire contract |
| `agent_host/codex_settings.py` | Codex model 与 reasoning effort 的无 I/O 选择规则 |
| `agent_host/codex_launch.py` | Codex session 启动配置与注入装配，不注册 manager 或持久化 binding |
| `cc_host/` / `codex_host/` | 两种原生 runtime 的进程、协议和事件适配 |
| `codex_host/session_types.py` | Codex Session 冻结配置、MCP 配置与 thread 事实解析 |
| `codex_host/transport_state.py` | Codex 客户端 pending response 与关闭清理状态 |
| `cc_host/history/` | CC 会话时间线回放、Workflow 快照与消息事件翻译 |
| `cc_host/checkpoint/` | CC 私有 Git checkpoint 的稳定 facade 与底层 plumbing |
| `cc_host/frontmatter.py` | CC skill 与 slash command 的轻量 frontmatter 解析 |
| `cc_host/schemas.py` | CC HTTP 请求与原生事件 wire contract |
| `cc_host/workflow_tree.py` | CC workflow 磁盘快照到 wire tree 的纯转换 |
| `codex_host/file_change_codec.py` | Codex fileChange 到前端 diff shape 的纯转换 |
| `model_os/` | Task、Episode、租约、事件日志与模型资源仲裁 |
| `model_os/episode_fold.py` / `work_item_fold.py` / `context_fold.py` | Reducer 的无 I/O 事件折叠策略 |
| `model_os/store_event_factory.py` / `store_projection.py` | Store 事件构造与公开状态投影 |
| `model_os/task_commands.py` | Task 创建、warm/foreground、等待与终态命令编排 |
| `model_os/episode_snapshot_codec.py` | EpisodeSnapshot payload codec 与写入前校验 |
| `model_os/episode_recovery.py` | Episode 恢复快照的纯事实折叠策略 |
| `model_os/context_codec.py` / `context_adapters.py` | Context journal codec 与 AgentEvent 标准化 |
| `model_os/work_broker/` | 模型资源仲裁、公开值对象、lease codec 与 SQLite schema |
| `model_os/work_broker/policy.py` / `usage_persistence.py` | WorkBroker 确定性策略与事务内 usage 持久化 |
| `memory/` | 长期记忆、检索、日记/笔记提炼与会话来源仓储 |
| `profile/` | 用户画像、建议队列、画像提炼、重校准与 HTTP 接口 |
| `quota/` | provider 额度读取与归一化 |
| `telemetry/` | 本地 span/metric 白名单、W3C 上下文、Agent/runtime/MCP 关联、异步采集、独立 SQLite、聚合、清理、运行埋点和退出标记导入 |
| `statistics/` | 统计时间窗、统一质量响应和只读 API |
| `statistics/runtime/` | 桌面启动退出、sidecar、FastAPI、SSE、SQLite 和资源 owner 的运行统计 read model |
| `statistics/calls/` | 稳定游标调用列表、有限跨 trace 图、坏关系降级和去正文详情 read model |
| `quota/glm/` | GLM quota 的稳定 client、payload 解析与 httpx transport |
| `todo_loop/` | todo 展开与持续推进辅助 |
| `cards/` / `review/` / `feynman/` | 卡片提取、复习和费曼学习 |
| `garden/` / `player/` / `pet/` / `events/` | 花园、玩家状态、宠物和事件系统 |
| `llm/` | 模型客户端、prompt 与输出过滤 |
| 各领域的 `models.py` / `schemas.py` | 领域值对象与 HTTP/LLM 数据边界 |
| `schemas/` | 旧 Python import 路径的兼容 re-export，不拥有模型定义 |

领域模块通常把 HTTP、业务逻辑和持久化分别放在 `routes.py`、`service.py` 与 `repository.py`；实际文件按领域需要增减。

### Memory 内部边界

| 路径 | 职责 |
|---|---|
| `memory/review_job.py` | daily review 的稳定入口、日期解析与进程锁 |
| `memory/daily_review/` | 提炼编排、runtime 适配、共享处理器、调度与持久化工作目录 |
| `memory/daily_review/requests.py` | 持久登记用户会话关闭后的即时 review 请求 |
| `memory/daily_review/adapters/` | 分别用 `claude.py` 和 `codex.py` 解释字节水位或 turn fragment，并适配为统一 ReviewUnit |
| `memory/daily_review/processor.py` | 统一执行 refine、日期校验、持久化、水位推进和 judge |
| `memory/daily_review/sources/` | 并列定义 Claude Code 字节区间与 Codex turn journal 的历史上下文、处理目标、可用性和统一渲染 |
| `memory/scheduling.py` | memory 调度器共用的纯时间计算 |
| `memory/compress/` | daily、weekly、monthly 的生成、来源校验、预算与缓存生命周期 |
| `memory/compress/weekly_generation.py` | Weekly v3 结构化输出、source day/section 覆盖与 800 字完整 item 预算 |
| `memory/compress/rollup_sources.py` / `monthly_generation.py` | 周月上游来源/hash 与月记完整句分层生成 |
| `memory/regeneration/` | 日周月重生成的只读计划、staging 续跑、manifest 与显式原子发布 |
| `memory/persist/` | draft 落盘报告、note 更新、meta 产物与完成 manifest 编排 |
| `memory/mcp_server.py` / `memory/mcp/` | memory MCP 稳定入口、请求分发与搜索/读取/反馈处理器 |
| `memory/judgements/` | judgement 冻结模型、严格 codec、文件仓储与未知 ID 过滤 |
| `memory/judge/` | judgement 证据汇总、宽松 draft 解析与 agent 生命周期 |
| `memory/recompute/` | note 效果模型、会话级证据聚合与缓存回写 |
| `memory/cli/` | memory 命令参数、分发与维护操作 |
| `memory/north_star/` | note 健康与会话级使用质量指标 |
| `memory/prompt/` | refine 提炼与 daily compression prompt 契约 |
| `memory/sessions_repo/` | session 数据契约、SQLite schema/连接，以及 `claude`、`codex`、`review_requests` 三个作用域仓储 |
| `memory/store/` | file-backed memory 的 notes、diary、episode 与 Markdown codec |
| `memory/tidy/` | tidy 数据契约、计划校验、快照应用、LLM 计划与周期任务编排 |
| `memory/tidy_scheduler/` | tidy 的时间计算、成功门禁、应用内生命周期与显式补跑 |
| `memory/tidy_state/` | tidy 水位模型、原子持久化与已完成周期计算 |
| `memory/dictionary.py` | dictionary 派生、校验和发布的稳定入口 |
| `memory/dictionary_check/` | dictionary 纯一致性评估与只读文件快照 |
| `memory/dictionary_index/` | LLM 聚类/渲染与原子文件发布 |
| `memory/draft/` | 提炼 draft 的稳定模型、宽松解析、硬校验与 procedure 软告警 |
| `memory/draft/episode.py` | Episode kind-specific item、严格解析与 daily 文本投影 |
| `memory/daily_review/agent.py` | 消费已划分 context/target 的统一 ReviewSource，按目标计算成本并驱动、校验提炼草稿 |

### Profile 内部边界

| 路径 | 职责 |
|---|---|
| `profile/models.py` / `document.py` | Profile 与建议值对象、`profile.md` 正文编解码和校验 |
| `profile/repository.py` | Profile 文件读写与历史快照 |
| `profile/suggestions/` | 建议编解码、带锁文件队列与状态策略 |
| `profile/distill/` | prompt、agent 驱动、门禁、批处理、独立水位与应用内调度 |
| `profile/distill/adapters/` | Claude Code 字节水位与 Codex turn 分别适配为统一候选，并各自推进 Profile 处理记录 |
| `profile/distill/processor.py` | 统一加载去重上下文、构造 prompt、驱动 Agent 并执行证据门禁 |
| `profile/distill/sources/` | 统一描述 context/target，并分别构造 Claude 与 Codex 来源和校验 Codex target 用户证据 |
| `profile/distill/state.py` | 在同一兼容状态文件中保存 Claude 字节水位和 Codex turn 处理记录 |
| `profile/recalibration/` | 历史计划、隔离重放、manifest 与报告产物 |
| `profile/routes.py` / `schemas.py` / `service.py` | Profile HTTP 接口、DTO 与依赖装配 |

## 前端

| 路径 | 职责 |
|---|---|
| `web/src/App.tsx` | 页面入口与顶层工具切换 |
| `web/desktop/` | Electron main、preload、sidecar 监督、诊断页与桌面 smoke |
| `web/desktop/desktopDataPaths.ts` | 解析正式、日常开发与隔离开发的数据、日志和 Electron userData 路径 |
| `web/shared/desktop-contracts.ts` | Electron main、preload 与 renderer 共用的桌面 IPC 类型契约 |
| `web/src/platform/` | browser/desktop 平台接口与统一后端 transport |
| `web/src/agent/domain/` | Agent session、turn、timeline item 与纯 reducer 的唯一 owner |
| `web/src/agent/application/` | Agent Zustand store、会话命令、连接生命周期和 selector 的唯一 owner |
| `web/src/agent/transport/` | Trowel Agent HTTP、SSE 与 wire DTO 的唯一 owner |
| `web/src/agent/runtimes/` | capability 协议、Claude Code/Codex 同级 presentation adapter 与各自专属 UI |
| `web/src/agent/runtimes/shared/` | 两个 runtime 都按相同语义使用的 capability 组合、路径和工具输出展示；单一 runtime 的代码不得进入 |
| `web/src/agent/ui/` | 双 runtime 共用的会话 shell、消息列表、工作目录选择器与样式入口 |
| `web/src/agent/index.ts` | 前端 Agent 领域的稳定公开 facade |
| `web/src/api/` | 其他产品 API；`api/cc.ts` 只保留 Claude Code 专属 HTTP 操作 |
| `web/src/stores/` | Agent 之外的产品 Zustand store；旧 `ccStore`、`ccReducer` 与 selector 路径已删除 |
| `web/src/components/` | 按 cards、cc、garden、profile 等领域组织的页面组件；runtime 专属展示从 `agent/runtimes` facade 读取 |
| `web/src/styles/` | 全局 token 与样式 |
| `web/src/statistics/` | 五个统计页签共用的 DTO、transport、store、日期状态，以及 Agent、Memory、运行、调用详情的生产容器和纯展示组件 |
| `web/src/development/*-statistics/` | 复用生产组件和脱敏样例的独立统计页预览入口 |
| `web/shared/telemetry-*` | Electron 与 renderer 共用的版本化批次和有界 batcher |
| `web/src/__tests__/` | Vitest 组件和状态测试 |
| `web/scripts/check-module-comments.mjs` | 检查生产 TypeScript 模块是否以中文职责说明开头 |

## 测试

- `tests/<domain>/` 对应后端领域，领域测试不平铺在一级目录；
- `tests/integration/` 放跨领域端到端测试；
- `tests/contracts/` 冻结 OpenAPI、CLI、SSE event type 和 SQLite schema；
- `tests/fixtures/` 放跨领域共享 fixture；
- `tests/` 一级只保留包入口与共享 `conftest.py`。

生成目录、缓存、虚拟环境、真实 fixture 和运行时数据不进入这张地图。
