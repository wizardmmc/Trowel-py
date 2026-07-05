/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // dev: vite :5173 proxies /api to the uvicorn backend on :8000. Release
    // build is same-origin so this is irrelevant there.
    proxy: { "/api": "http://localhost:8000" },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
