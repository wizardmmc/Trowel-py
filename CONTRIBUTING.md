# 参与贡献

Trowel 使用 Issue、独立分支、Pull Request、自动检查和 Review 管理改动，
最终通过 Squash merge 写入受保护分支。

## 开始前

先搜索已有 Issue，避免重复工作。Bug 应说明复现方式、预期结果、实际结果和
运行环境；新功能应先确认范围和验收标准。准备处理已有 Issue 时，在 Issue 下
留言认领，避免多人重复实现。

每个 Issue 使用一个独立分支和 Pull Request。无关修改应拆到其他 Issue，避免
扩大 Review 范围。

## 开发环境

项目需要 Python 3.13、[uv](https://docs.astral.sh/uv/) 和
[Bun](https://bun.sh/)。克隆仓库后安装锁定依赖：

```bash
uv sync --locked
cd web
bun install --frozen-lockfile
```

运行应用时，另需从 `config.example.toml` 创建本地 `config.toml` 并填写模型服务
配置。测试不应依赖私人配置。

源码入口见 [`directory.md`](./directory.md)。

## 创建分支

普通修复、文档和独立功能从 `main` 创建分支。属于某个 milestone 的工作从对应
的 `milestone-*` 分支创建，并将 Pull Request 提交回该 milestone。Issue 中的目标
分支说明优先。

```bash
git switch main
git pull --ff-only
git switch -c fix/short-description
```

分支名称使用 `feat/`、`fix/`、`docs/` 或 `chore/` 前缀。`main` 和
`milestone-*` 均为受保护分支，不直接提交或强制推送。

## 本地验证

先运行与改动直接相关的测试，再运行对应的完整检查。

后端：

```bash
uv run --no-sync python -m pytest --ignore=tests/contracts
uv run --no-sync ruff check trowel_py tests
```

前端：

```bash
cd web
bun run typecheck
bun run test
bun run build
```

公开契约依赖前端构建产物。在完成前端构建后，从仓库根目录运行：

```bash
uv run --no-sync python -m pytest tests/contracts
```

依赖发生变化时，应提交对应的 `uv.lock` 或 `web/bun.lock`。公开契约确实需要变化
时，应在 Pull Request 中单独说明变更内容和兼容性影响。

## 提交与 Pull Request

Commit message 包含简明标题和正文。正文说明本次改变了什么、为什么这样改以及
如何验证，不记录开发过程或与改动无关的通用论证。

工作尚未完成时创建 Draft Pull Request。准备 Review 时，Pull Request 应包含：

- 问题和影响；
- 改动范围；
- 实际运行的验证命令和结果；
- `Closes #<issue>` 或其他 Issue 关联。

Pull Request 标题和正文会成为最终 squash commit 的标题和正文。提交者应处理
Review 意见、解决对话并保持目标分支为最新状态。所有自动检查通过后，贡献者的
Pull Request 还需要一名具有写权限的协作者批准。

仓库只允许 Squash merge，并在合并后自动删除远端功能分支。仓库管理员自己的
Pull Request 仍须经过 PR 和自动检查；仅人工 approval 可以在 PR 内按维护者流程
绕过，该权限不用于合并其他贡献者未经 Review 的改动。

## 隐私边界

不得提交 `config.toml`、数据库、日志、本机录制、API key、绝对路径、私人会话
正文或额度信息。协议 fixture 应经过最小化和脱敏，并在测试中只保留证明契约所需
的字段。发现敏感信息进入提交历史时，应立即停止推送并通知维护者处理。
