# AGENTS.md — trowel-py

本文件只提供 fresh agent 开工所需的公共入口、命令和安全边界。当前任务由用户消息、
Issue 或 Pull Request 给出；每个开发者自己的 milestone、slice 和实验记录不进入仓库。

## 开工入口

按顺序读取：

1. [README.md](README.md)：产品与本地运行方式；
2. [directory.md](directory.md)：源码地图；
3. [docs/foundation/prd.md](docs/foundation/prd.md)：产品目标与边界；
4. [docs/foundation/development.md](docs/foundation/development.md)：开发、验证和文档规则；
5. 用户消息、Issue 或 Pull Request 中指定的当前任务。

代码、可执行测试和 Git 历史与文档冲突时，以代码、测试和 Git 历史为准，并同步修正
公共文档。

## 项目速览

- 后端：Python 3.13、FastAPI、sqlite3、Pydantic v2；
- 前端：React 19、Vite、Zustand、framer-motion；
- 会话运行时：Claude Code 与 Codex，经 `agent_host` 提供统一会话边界；
- 长期系统：Memory、Profile 与 Model OS。

## 常用验证

```bash
# 后端；pyproject 已限制只收集 tests/
.venv/bin/python -m pytest

# 公开契约
.venv/bin/python -m pytest tests/contracts

# 前端
cd web
bun run typecheck
bun run test
bun run build
```

公开契约确实需要变化时，先审查差异，再显式运行：

```bash
.venv/bin/python -m tests.contracts.public_contracts --update
```

全仓 `ruff` 与 `mypy` 仍有历史债务。新改文件必须做窄范围检查，不能用历史红项掩盖
新增问题。分支、Pull Request 和提交规则见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 不可破坏的边界

- route 和集成测试必须把数据库依赖替换到临时库或内存库，不能使用根目录的
  `trowel.db`；
- `config.toml` 含真实凭据；日志和本机录制可能含路径、会话正文或额度信息；
- Claude Code 真集成测试会启动 `claude -p`，默认被 `integration` marker 排除，只在
  普通终端显式运行；
- 第三方事件 shape 必须来自真实录制或上游源码，手写 fixture 不能证明未知协议；
- FastAPI 全局异常处理是统一错误 envelope 的通用 fallback，端点不要各自吞掉 LLM 异常；
  需要更严格隐私脱敏的领域可在 route class 统一映射，但必须保持同一 envelope；
- 展示组件保持纯 props，store 订阅和 transport 放在容器或 store 层；
- 未经人确认不提交、合并或推送，不直接修改稳定分支。

## 按领域继续读

| 任务范围 | 公共入口 |
|---|---|
| Claude Code、Codex、SSE、会话恢复、workflow | [Agent runtime 运行约束](docs/reference/agent-runtime.md) |
| Memory 写入、检索、watermark、MCP | [Memory 运行约束](docs/reference/memory-runtime.md) |
| Discussion、多参与者同步轮次、恢复、交接 | [Discussion 后端领域上下文](trowel_py/discussion/AGENTS.md) |
| Model OS、Task、Episode、lease、WorkBroker | [仓库地图的 Model OS 条目](directory.md#后端) |
| 前端生产界面 | [Trowel 前端设计语言](docs/foundation/front-end-design-language.md) |
| 项目流程、spec、注释 | [开发流程](docs/foundation/development.md) |

模块级公共说明只在稳定事实已经过代码、测试和真实证据复核后创建。历史原因优先查 Git
历史；公共入口不引用开发者本机的 slice、milestone、实验或归档笔记。
