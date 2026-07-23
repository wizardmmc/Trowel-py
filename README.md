<p align="center"><strong>trowel</strong> 把 AI 编程会话沉淀成可复用的长期记忆。</p>

<p align="center">
  <img src="./screenshots/tcc-screenshot.png" alt="tcc 会话界面" width="80%" />
</p>

Trowel 是本地桌面工具。它托管 Claude Code 会话，从会话中提炼日记和笔记，并在后续会话中提供相关记忆。

## 主要功能

- **tcc**：Claude Code 的桌面会话界面，支持消息、工具调用、提问、workflow 和历史恢复；
- **memory**：从会话中提炼可追溯的日记与笔记，并提供检索；
- **review**：把会话或粘贴内容整理成复习卡片；
- **garden**：用知识花园展示卡片和复习状态。

## 本地运行

需要 Python 3.13、[uv](https://docs.astral.sh/uv/) 和 Node.js。

后端：

```bash
uv sync
cp config.example.toml config.toml
# 在 config.toml 中填写所用模型服务的 api_key 和 base_url
uv run trowel-py
```

前端开发服务器：

```bash
cd web
npm install
npm run dev
```

前端开发服务器会把 `/api` 转发到 `http://localhost:8000`。

## 技术栈

- 后端：FastAPI、sqlite3、Pydantic v2；
- 前端：React 19、Vite、Zustand、framer-motion；
- 模型：Anthropic 兼容 API。

项目优先服务作者自己的本地工作流，当前不承诺开箱即用或完整覆盖 Claude Code 的全部能力。

MIT License，见 [LICENSE](./LICENSE)。
