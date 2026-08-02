/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createAgentServiceProxy } from './desktop/agentServiceProxy'

export default defineConfig({
  base: './',
  plugins: [
    react(),
    {
      name: 'trowel-agent-service-proxy',
      configureServer(server) {
        server.middlewares.use(
          createAgentServiceProxy({
            descriptorPath: process.env.TROWEL_DESKTOP_SERVICE_FILE,
            fallbackBaseUrl:
              process.env.TROWEL_API_FALLBACK_URL ?? 'http://localhost:8000',
          }),
        )
      },
    },
  ],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
