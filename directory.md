# 仓库地图

这是一张源码导航图，不展开实现历史，也不逐文件复制目录树。

## 根目录

| 路径 | 职责 |
|---|---|
| `README.md` | 产品介绍与运行方式 |
| `pyproject.toml` / `uv.lock` | Python 包、依赖和测试配置 |
| `config.example.toml` | 不含真实凭据的配置样例 |
| `trowel_py/` | FastAPI 后端与本地运行时 |
| `web/` | React 前端 |
| `tests/` | Python 测试与公开契约快照 |

本地设计文档、agent 指令、真实配置、数据库、日志和录制数据均被 gitignore，不属于公开仓库内容。

## 后端

| 路径 | 职责 |
|---|---|
| `app.py` | 组装 FastAPI、生命周期与路由 |
| `cli.py` | `trowel-py` 命令行入口 |
| `config.py` | 模型服务配置读取 |
| `db/` | 主数据库连接与 SQL 迁移 |
| `agent_host/` | Claude Code 与 Codex 的统一会话边界 |
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
| `memory/` / `profile/` | 长期记忆、检索、提炼与用户画像 |
| `quota/` | provider 额度读取与归一化 |
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
| `memory/daily_review/` | 提炼 agent、增量批处理、调度与持久化工作目录 |
| `memory/scheduling.py` | memory 调度器共用的纯时间计算 |
| `memory/profile_distill_job.py` | profile distill 的稳定兼容入口 |
| `memory/profile_distill/` | gate、agent 驱动、批处理、prompt、独立水位、重校准与应用内调度 |
| `memory/compress/` | daily 生成与缓存生命周期、weekly/monthly rollup 和兼容入口 |
| `memory/persist/` | draft 落盘报告、note 更新、meta 产物与完成 manifest 编排 |
| `memory/mcp_server.py` / `memory/mcp/` | memory MCP 稳定入口、请求分发与搜索/读取/反馈处理器 |
| `memory/judgements/` | judgement 冻结模型、严格 codec、文件仓储与未知 ID 过滤 |
| `memory/judge/` | judgement 证据汇总、宽松 draft 解析与 agent 生命周期 |
| `memory/recompute/` | note 效果模型、会话级证据聚合与缓存回写 |
| `memory/cli/` | memory 命令参数、分发与维护操作 |
| `memory/north_star/` | note 健康与会话级使用质量指标 |
| `memory/prompt/` | refine 提炼与 daily compression prompt 契约 |
| `memory/profile_suggestions/` | 画像建议编解码、带锁文件队列与状态策略入口 |
| `memory/sessions_repo/` | session 数据契约、SQLite schema/连接与 registry 查询 |
| `memory/store/` | file-backed memory 的 notes、diary、episode、profile 与 Markdown codec |
| `memory/tidy/` | tidy 数据契约、计划校验、快照应用、LLM 计划与周期任务编排 |
| `memory/tidy_scheduler/` | tidy 的时间计算、成功门禁、应用内生命周期与显式补跑 |
| `memory/tidy_state/` | tidy 水位模型、原子持久化与已完成周期计算 |
| `memory/dictionary.py` | dictionary 派生、校验和发布的稳定入口 |
| `memory/dictionary_check/` | dictionary 纯一致性评估与只读文件快照 |
| `memory/dictionary_index/` | LLM 聚类/渲染与原子文件发布 |
| `memory/draft/` | 提炼 draft 的稳定模型、宽松解析、硬校验与 procedure 软告警 |

## 前端

| 路径 | 职责 |
|---|---|
| `web/src/App.tsx` | 页面入口与顶层工具切换 |
| `web/src/api/` | HTTP、SSE 与 wire types |
| `web/src/stores/` | Zustand 状态与事件 reducer |
| `web/src/components/` | 按 cards、cc、garden、profile 等领域组织的组件 |
| `web/src/styles/` | 全局 token 与样式 |
| `web/src/__tests__/` | Vitest 组件和状态测试 |

## 测试

- `tests/<domain>/` 对应后端领域，领域测试不平铺在一级目录；
- `tests/integration/` 放跨领域端到端测试；
- `tests/contracts/` 冻结 OpenAPI、CLI、SSE event type 和 SQLite schema；
- `tests/fixtures/` 放跨领域共享 fixture；
- `tests/` 一级只保留包入口与共享 `conftest.py`。

生成目录、缓存、虚拟环境、真实 fixture 和运行时数据不进入这张地图。
