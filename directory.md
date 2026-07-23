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
| `cc_host/` / `codex_host/` | 两种原生 runtime 的进程、协议和事件适配 |
| `model_os/` | Task、Episode、租约、事件日志与模型资源仲裁 |
| `memory/` / `profile/` | 长期记忆、检索、提炼与用户画像 |
| `quota/` | provider 额度读取与归一化 |
| `todo_loop/` | todo 展开与持续推进辅助 |
| `cards/` / `review/` / `feynman/` | 卡片提取、复习和费曼学习 |
| `garden/` / `player/` / `pet/` / `events/` | 花园、玩家状态、宠物和事件系统 |
| `llm/` | 模型客户端、prompt 与输出过滤 |
| `schemas/` | 跨领域 Pydantic 数据模型 |

领域模块通常把 HTTP、业务逻辑和持久化分别放在 `routes.py`、`service.py` 与 `repository.py`；实际文件按领域需要增减。

### Memory 内部边界

| 路径 | 职责 |
|---|---|
| `memory/review_job.py` | daily review 的稳定入口、日期解析与进程锁 |
| `memory/daily_review/` | 提炼 agent、增量批处理、调度与持久化工作目录 |
| `memory/scheduling.py` | memory 调度器共用的纯时间计算 |
| `memory/dictionary.py` | dictionary 派生、校验和发布的稳定入口 |
| `memory/dictionary_index/` | LLM 聚类/渲染与原子文件发布 |

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
