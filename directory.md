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
│   ├── design/            前端 review（/plan-design-review 产出，front-end/）
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
│   ├── garden/            花园领域
│   ├── pet/               宠物领域（brain / mood）
│   ├── events/            事件引擎 + 冷却 + 奖励
│   ├── player/            玩家领域（level / xp / coins / streak）
│   ├── feynman/           费曼模式
│   ├── llm/               LLM 客户端 + prompts + filter
│   └── schemas/           Pydantic 数据模型（api / card / event / extracted_card / feynman / follow_up）
│
├── web/                   前端：React 19 + Vite + Zustand + framer-motion
│   └── src/
│       ├── App.tsx        容器组件，订阅 store
│       ├── api/           API 客户端（fetch 封装）
│       ├── components/    展示/容器组件，按领域分子目录（cards/ 等）
│       ├── stores/        Zustand store（cardStore 等）
│       └── styles/        样式
│
├── tests/                 pytest 测试：conftest.py + 按领域（events/ pet/）+ 跨领域 e2e
│
├── trowel.db              sqlite 数据文件（gitignored）
├── config.toml            配置，含密钥（gitignored）
└── logs/ .venv/ 等        运行时产物（gitignored）
```
