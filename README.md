<p align="center"><strong>trowel</strong> 把 AI 编程会话沉淀成可复用的长期记忆。</p>

<p align="center">
  <img src="./screenshots/tcc-screenshot.png" alt="tcc 会话界面" width="80%" />
</p>

Trowel 是本地桌面工具。它托管 Claude Code 与 Codex 会话，从会话中提炼日记和
笔记，并在后续会话中提供相关记忆。

## 主要功能

- **tcc**：Claude Code 与 Codex 的桌面会话界面，支持消息、工具调用、提问、
  workflow 和历史恢复；
- **memory**：从会话中提炼可追溯的日记与笔记，并提供检索；
- **review**：把会话或粘贴内容整理成复习卡片；
- **garden**：用知识花园展示卡片和复习状态。

## 下载安装

首个公开预览版支持 macOS 26.5 和 Apple Silicon，可从
[GitHub Releases](https://github.com/wizardmmc/Trowel-py/releases) 下载 DMG。安装包
已经包含 Electron Host、React 界面和 Python sidecar，运行时不需要系统 Python、
Node 或 Bun；Claude Code 与 Codex CLI 按实际使用需要单独安装。

当前预览包使用 ad-hoc 签名，尚未完成 Apple 公证。macOS 可能阻止普通首次打开；
确认下载来源并核对附件中的 SHA-256 后，可按 Apple 的
[安全设置说明](https://support.apple.com/guide/mac-help/open-a-mac-app-from-an-unidentified-developer-mh40616/mac)
在“隐私与安全性”中选择仍要打开。

## 本地开发

### 准备依赖

需要 Python 3.13、[uv](https://docs.astral.sh/uv/) 和 [Bun](https://bun.sh/)。

```bash
uv sync --locked --group dev
cd web
bun install --frozen-lockfile
cd ..
```

从 `config.example.toml` 创建不进入 Git 的本地配置：

```bash
cp config.example.toml config.toml
# 在 config.toml 中填写所用模型服务的 api_key 和 base_url
```

### 前后端分开运行

后端在仓库根目录启动：

```bash
uv run trowel-py
```

前端开发服务器在另一个终端启动：

```bash
cd web
bun run dev
```

前端开发服务器会把 `/api` 转发到 `http://localhost:8000`。

### 桌面开发模式

桌面开发模式会自动启动随机端口的 Vite、Electron 和 Python sidecar：

```bash
cd web
bun run desktop:dev
```

该命令与正式 App 共用 `~/Library/Application Support/Trowel/data`，因此正式 App
必须先完整退出。只做 UI 或故障实验时使用隔离数据目录：

```bash
cd web
bun run desktop:dev:isolated
```

只验收统计页面时可直接读取正式 App 的真实数据，并把所有业务写接口关闭：

```bash
cd web
bun run desktop:dev:observe
```

观察模式只显示“统计”入口，不启动 Agent runtime、Memory/Profile 后台任务，也不向
正式数据库写入遥测。正式 App 可以同时运行；统计数字会随正式 App 的数据变化，并在
“运行”页每 5 秒刷新一次。

### 构建本地 macOS App

以下命令构建包含冻结 Python sidecar 的本地 `.app`，随后直接打开构建结果：

```bash
cd web
bun run package:mac
open out/Trowel-darwin-arm64/Trowel.app
```

`package:mac` 不生成 DMG 和发布附件。完整候选、签名、公证与 GitHub Pre-release
流程见 [`web/packaging/RELEASE.md`](./web/packaging/RELEASE.md)。

### 单进程浏览器版

单进程运行需要先构建前端。以下命令在已激活的 Python 3.13 环境中执行
editable install：

```bash
./scripts/build-package.sh
python -m pip install -e .
trowel-py
```

更新已有的 `main` 副本时，使用快进更新保留本地改动保护：

```bash
git switch main
git fetch origin
git merge --ff-only origin/main
./scripts/build-package.sh
python -m pip install -e .
```

`git reset --hard origin/main` 会永久丢弃未提交改动，不作为常规更新方式。

## 技术栈

- 后端：FastAPI、sqlite3、Pydantic v2；
- 前端：React 19、Vite、Zustand、framer-motion；
- 运行工具：Claude Code、Codex；
- 模型服务：Anthropic 兼容 API 与 Codex 原生配置。

开发与提交规则见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

MIT License，见 [LICENSE](./LICENSE)。
