# Web 前端

Trowel 的 React 前端，使用 Vite、TypeScript、Zustand 和 Vitest。

## 开发

后端默认运行在 `http://localhost:8000`。Vite 会把 `/api` 请求转发到该地址。

```bash
npm install
npm run dev
```

## 验证

```bash
npm run typecheck
npm run test
npm run build
```

## 目录

- `src/api/`：HTTP、SSE 和 wire types；
- `src/stores/`：状态与事件 reducer；
- `src/components/`：按功能领域组织的组件；
- `src/__tests__/`：组件、store 和协议适配测试。

发布构建由后端同源提供，开发代理只用于本地 Vite 服务。
