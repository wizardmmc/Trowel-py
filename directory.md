# 项目目录

全项目结构。标「(gitignored)」的是本地私有、不进 git。每个后端领域模块通常是同样三件套：`repository.py`（数据访问）+ `service.py`（业务逻辑）+ `routes.py`（HTTP 端点），有的加 `types.py` / `config.py`。
tip：可修改，每完成一个slice后检查同步。

```
trowel-py/
├── CLAUDE.md              项目宪法：原则 + 链接（gitignored）
├── CLAUDE copy.md         过往教学协议，历史底稿（gitignored）
├── AGENTS.md              fresh agent 上手速览 + gotcha（gitignored）
├── directory.md           本文件
├── ReadMe.md              仓库入口
├── Learn.md               早期 slice 学习记录（gitignored）
├── pyproject.toml         Python 依赖（uv）
│
├── docs/                  设计与开发文档，整体 gitignored、本地私有
│   ├── foundation/        产品根基：prd.md（并非一成不变，核心作用是锚定大致方向）/ development.md / adr/
│   ├── slices/            SDD 的 slice spec（如 021.md）
│   ├── design/            前端设计稿（/plan-design-review 产出 + slice mockup）
│   │   └── front-end/     slice 前端 mockup（{feature}-{date}.html，如 ask-user-question-20260704.html）+ review md
│   ├── training-log-m1.md / m2.md / m3.md    各阶段训练日志（进度 + 知识点）
│   └── training-log-status.md                进度状态
│
├── trowel_py/             后端：FastAPI + sqlite3 + Pydantic v2
│   ├── app.py             FastAPI app factory（纯函数，无副作用）
│   ├── server.py          入口，启动 uvicorn
│   ├── config.py          配置加载
│   ├── db/                数据库连接 + 迁移（connection.py / migrate.py / migrations/）
│   ├── cards/             卡片领域：提取、存储、去重、re-explain
│   ├── review/            复习领域 + 调度器（scheduler.py）
│   ├── memory/            记忆领域：review_scheduler.py（app 内每日 review 调度，slice-046，替代已删的 launchd schedule.py）+ review_job（每日提炼，slice-040）+ judge/judgements/judge_prompt（判效 agent：会话笔记「用没用/有用没用/该用没用」+ 三指标，slice-053）+ sessions_repo/hooks/store/access_log/north_star 等（slice-038+）
│   ├── garden/            花园领域
│   ├── pet/               宠物领域（brain / mood）
│   ├── events/            事件引擎 + 冷却 + 奖励
│   ├── player/            玩家领域（level / xp / coins / streak）
│   ├── feynman/           费曼模式
│   ├── llm/               LLM 客户端 + prompts + filter
│   ├── cc_host/           CC 子进程 host（slice022）；history.py 解析 jsonl→同构 trowel 事件（slice023-web）；workflow_watcher.py 读 wf_<runId>.json 渲染 workflow 进度树（slice-036）；subagent_usage.py 从 subagent transcript 累加 token（slice-036 D 层）
│   ├── agent_host/       host-neutral Session Hub（slice-072）：binding.py（SessionBinding/Runtime）+ store.py（json 持久化）+ hub.py（路由 CC/Codex、runtime 冻结、交叉 resume 拒绝）+ routes.py（/api/agent/*）+ schemas.py；cc_host 复用 open_cc_session / close_cc_session
│   ├── model_os/         Model OS 内核骨干（M8 slice-084）：独立 SQLite+WAL Store + append-only 事件/决策日志 + 纯函数 reducer + payload 脱敏。types.py（枚举+frozen dataclass：WorkItem/EventEnvelope/DecisionRecord/Lease/Provenance 强度排序）+ redaction.py + reducer.py（Snapshot/reduce_event，provenance 不允许静默升级）+ store.py（事务/CAS lease/append/read_snapshot/replay）。后续 slice-085+ 在此之上建 Self/Task/Episode/Scheduler
│   └── schemas/           Pydantic 数据模型（api / card / event / extracted_card / feynman / follow_up / cc_host）
│
├── web/                   前端：React 19 + Vite + Zustand + framer-motion
│   └── src/
│       ├── App.tsx        容器组件，订阅 store
│       ├── api/           API 客户端（fetch 封装）；cc.ts/ccStream.ts/ccTypes.ts = CC 会话（slice023-web）；agent.ts = host-neutral /api/agent（slice-072）
│       ├── components/    展示/容器组件，按领域分子目录（cards/ cc/ 等）
│       ├── stores/        Zustand store；ccStore.ts = host-neutral 多 session 壳（CC+Codex，slice-072）；ccReducer.ts/codexReducer.ts = 事件 reducer（CC / Codex）
│       └── styles/        样式
│
├── tests/                 pytest 测试：conftest.py + 按领域（events/ pet/）+ 跨领域 e2e
│
├── trowel.db              sqlite 数据文件（gitignored）
├── config.toml            配置，含密钥（gitignored）
└── logs/ .venv/ 等        运行时产物（gitignored）
```
