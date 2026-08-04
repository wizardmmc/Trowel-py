/** 挂载 Trowel 主应用并加载全局样式。 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import 'lxgw-wenkai-webfont/style.css'
import 'katex/dist/katex.min.css'
import './styles/index.css'
import App from './App.tsx'
import { initializePlatform } from './platform'
import { configureTransportTelemetry } from './platform/transport'
import { createRendererTelemetryPort } from './statistics/telemetryPort'
import { recordRendererReady } from './statistics/rendererTelemetry'

async function bootstrapRenderer() {
  await initializePlatform()
  const telemetry = import.meta.env.VITE_TROWEL_INSPECTION_MODE === '1'
    ? null
    : createRendererTelemetryPort()
  configureTransportTelemetry(telemetry)
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
  requestAnimationFrame(() => {
    window.__TROWEL_RENDERER_READY__ = true
    if (telemetry) {
      recordRendererReady(telemetry, performance.timeOrigin, new Date())
      void telemetry.flush()
    }
  })
  window.addEventListener('pagehide', () => {
    configureTransportTelemetry(null)
    if (telemetry) void telemetry.drain(100)
  }, { once: true })
}

void bootstrapRenderer().catch(() => {
  const root = document.getElementById('root')
  if (root) root.textContent = 'Trowel 无法连接后台服务，请重新启动应用。'
})
